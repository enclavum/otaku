"""Shared plumbing for the live smokes (scenarios/live): one real app
over one real provider, plus the skip-if-absent checks every module
opens with. A smoke never fails because a server is off or a key is not
set — it skips, and says why."""

import os
from pathlib import Path

import httpx
import pytest

from otaku.backend.paths import Paths
from otaku.providers import ALL_CLIENTS, ModelState, UnreachableError
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
    the story itself goes to the live endpoint."""
    root = tmp_path / "state"
    paths = Paths.resolve(root)
    paths.ensure_tree()
    providers = {provider_config.name: provider_config}
    write_atomic(paths.config_file, config_mod.Config().to_toml())
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


def case_key(engine: str, var: str, required: frozenset[str] | set[str]) -> str:
    """The key an engine's case carries: required for a catalog (pytest
    skips without it), optional for an engine that may or may not demand
    one, the one omlx's own autoconfiguration reads off the machine, none
    for the rest."""
    if engine == "omlx":
        return OmlxClient.autoconfigure().api_key
    if not var:
        return ""
    return require_env(var) if var in required else os.environ.get(var, "")


def case_model(engine: str, url: str, key: str, named: str) -> str:
    """The model a case plays, as the engine's own client lists it (the
    name the client's `model` answers to — Kobold strips its prefix, say),
    the server probed first: a server that is down skips the case, as the
    engine's own module skips. omlx plays a LOADED model unless one is
    named (its listing carries the unloaded too, and a smoke does not
    wait on a load); the rest play the named one, else the first listed."""
    client = ALL_CLIENTS[engine](ProviderConfig(name=engine, url=url, api_key=key))
    try:
        rows = client.models.list(timeout=5.0)
    except UnreachableError:
        pytest.skip(f"no server at {url}")
    if named:
        # As listed: Ollama tags a bare name ":latest".
        return next((r.name for r in rows if r.name in (named, f"{named}:latest")), named)
    if engine == "omlx":
        rows = [row for row in rows if row.state is ModelState.LOADED]
        if not rows:
            pytest.skip("no model loaded in omlx")
    if not rows:
        pytest.skip(f"{url} lists no models")
    return rows[0].name
