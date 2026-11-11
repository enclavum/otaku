"""Shared plumbing for the live smokes (scenarios/live): one real app
over one real provider, plus the skip-if-absent checks every module
opens with, and the dictation smoke every provider that dictates runs —
a sentence spoken by this machine's own voice, written down by the
provider's dictation model. A smoke never fails because a server is off,
a key is not set or a provider offers no dictation model — it skips, and
says why."""

import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from otaku.backend.api import ModelRef, ModelRole
from otaku.backend.app.paths import Paths
from otaku.providers import ALL_CLIENTS, ModelKind, ModelState, UnreachableError
from otaku.providers.clients.omlx import OmlxClient
from otaku.settings import config as config_mod
from otaku.settings import providers as providers_mod
from otaku.settings import write_atomic
from otaku.settings.providers import ProviderConfig
from scenarios.support.harness import App, launch
from scenarios.support.server import ModelServer


def live_app(
    tmp_path: Path, server: ModelServer, provider_config: ProviderConfig, model: str
) -> App:
    """The real app over `provider_config`, set to play `model`. The scripted
    `server` carries only the harness plumbing (its "generic" provider);
    the story itself goes to the live endpoint — a story of its own, no
    sample seeded for the launch to land in."""
    root = tmp_path / "state"
    paths = Paths.resolve(root)
    paths.ensure_tree()
    providers = {provider_config.name: provider_config}
    write_atomic(paths.config_file, config_mod.Config(seed_sample=False).to_toml())
    write_atomic(paths.providers_file, providers_mod.render(providers))
    return launch(root, server, spec=f"{provider_config.name}/{model}")


def first_model(url: str, api_key: str = "") -> str:
    """The first model a live /v1 endpoint lists; pytest.skip when the
    server is down or serves nothing."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        data = httpx.get(f"{url}/models", headers=headers, timeout=5.0).json()["data"]
    except Exception:
        pytest.skip(f"no server at {url}")
    if not data:
        pytest.skip(f"{url} lists no models")
    return str(data[0]["id"])


def require_env(name: str) -> str:
    """The env var's value; pytest.skip when it is not set."""
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} is not set")
    return value


def case_key(provider: str, var: str, required: frozenset[str] | set[str]) -> str:
    """The key a provider's case carries: required for a catalog (pytest
    skips without it), optional for a provider that may or may not demand
    one, the one omlx's own autoconfiguration reads off the machine, none
    for the rest."""
    if provider == "omlx":
        return OmlxClient.autoconfigure().api_key
    if not var:
        return ""
    return require_env(var) if var in required else os.environ.get(var, "")


def case_model(provider: str, url: str, key: str, named: str) -> str:
    """The model a case plays, as the provider's own client lists it (the
    name the client's `model` answers to — Kobold strips its prefix, say),
    the server probed first: a server that is down skips the case, as the
    provider's own module skips. omlx plays a LOADED model unless one is
    named (its listing carries the unloaded too, and a smoke does not
    wait on a load); the rest play the named one, else the first listed."""
    client = ALL_CLIENTS[provider](ProviderConfig(name=provider, url=url, api_key=key))
    try:
        rows = client.models.list(timeout=5.0)
    except UnreachableError:
        pytest.skip(f"no server at {url}")
    if named:
        # As listed: Ollama tags a bare name ":latest".
        return next((r.name for r in rows if r.name in (named, f"{named}:latest")), named)
    if provider == "omlx":
        rows = [row for row in rows if row.state is ModelState.LOADED]
        if not rows:
            pytest.skip("no model loaded in omlx")
    if not rows:
        pytest.skip(f"{url} lists no models")
    return rows[0].name


# What a dictation smoke says aloud, and the words of it that must come
# back — most of them, as a model may spell a word its own way.
SPOKEN = "The quick brown fox jumps over the lazy dog."
_HEARD = ("quick", "brown", "fox", "jumps", "lazy", "dog")


def spoken_wav(tmp_path: Path) -> bytes:
    """`SPOKEN` in this machine's own voice, as a 16 kHz mono WAV — macOS's
    `say`, converted by `afconvert`; pytest.skip where either is absent."""
    if not (shutil.which("say") and shutil.which("afconvert")):
        pytest.skip("no `say` to speak the sentence with")
    aiff, wav = tmp_path / "spoken.aiff", tmp_path / "spoken.wav"
    subprocess.run(["say", "-o", str(aiff), SPOKEN], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
        check=True,
    )
    return wav.read_bytes()


def dictation_model(app: App, provider: str, named: str) -> str:
    """The model a dictation smoke pins: `named`, else the first `provider`
    files as a dictation model; pytest.skip when it files none."""
    if named:
        return named
    listing = app.api.providers.list(scope=provider)
    rows = [
        model.name
        for row in listing.providers
        if row.id == provider
        for model in row.models
        if ModelKind.DICTATION in model.kinds
    ]
    if not rows:
        pytest.skip(f"{provider} offers no dictation model")
    return rows[0]


def assert_written_down(app: App, provider: str, model: str, audio: bytes) -> None:
    """`audio` written down by `model`, pinned for dictation as a reader
    pins it: most of what was said comes back."""
    app.api.app.update(models={ModelRole.DICTATION: ModelRef(provider, model)})
    text = app.api.app.dictation.create(audio=audio, audio_format="wav").value.text.lower()
    heard = [word for word in _HEARD if word in text]
    assert len(heard) >= 4, f"heard {text!r}"
