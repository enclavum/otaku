"""The generic provider's smokes: every engine otaku has a native client
for, reached through the protocol alone — the section named "generic",
the engine's own url pasted in. What must hold everywhere: the models
list, a turn streams and persists. Marked `live`; each case skips itself
when its server is down or its key is not set (scripts/live-providers.sh
launches the local engines; OPENROUTER_API_KEY / NANOGPT_API_KEY reach
the catalogs, one short prompt to a cheap model).
"""

import os
from pathlib import Path

import pytest

from otaku.backend.api import providers as api_providers
from otaku.providers.clients.omlx import OmlxClient
from otaku.settings.providers import ProviderConfig
from scenarios.support.live import case_key, case_model
from scenarios.support.live import live_app as build_app

pytestmark = pytest.mark.live

# (engine, url, the env var of its key or "", the env var naming a model
# or a default) — the same choices the engine's own module makes, so a
# case costs what that module's does.
CASES = [
    ("llamacpp", "http://127.0.0.1:8080/v1", "", ""),
    ("koboldcpp", "http://127.0.0.1:5001/v1", "", ""),
    (
        "ollama",
        "http://127.0.0.1:11434/v1",
        "",
        os.environ.get("OTAKU_TEST_MODEL", "ollama/gemma3").partition("/")[2],
    ),
    (
        "omlx",
        os.environ.get("OTAKU_LIVE_OMLX_URL", OmlxClient.autoconfigure().url),
        "",
        os.environ.get("OTAKU_LIVE_OMLX_MODEL", ""),
    ),
    (
        "lmstudio",
        "http://127.0.0.1:1234/v1",
        "LMSTUDIO_API_KEY",
        os.environ.get("OTAKU_LIVE_LMSTUDIO_MODEL", ""),
    ),
    (
        "openrouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        os.environ.get("OTAKU_LIVE_OPENROUTER_MODEL", "openai/gpt-4o-mini"),
    ),
    (
        "nanogpt",
        "https://nano-gpt.com/api/v1",
        "NANOGPT_API_KEY",
        os.environ.get("OTAKU_LIVE_NANOGPT_MODEL", "gpt-4o-mini"),
    ),
]
_REQUIRED_KEYS = {"OPENROUTER_API_KEY", "NANOGPT_API_KEY"}  # a catalog answers nothing without one


class TestGeneric:
    @pytest.mark.parametrize(
        ("engine", "url", "key_var", "model"), CASES, ids=[c[0] for c in CASES]
    )
    def test_every_engine_lists_and_plays_through_the_protocol_alone(
        self, tmp_path: Path, server, engine: str, url: str, key_var: str, model: str
    ) -> None:  # type: ignore[no-untyped-def]
        key = case_key(engine, key_var, _REQUIRED_KEYS)
        model = case_model(engine, url, key, model)
        app = build_app(
            tmp_path, server, ProviderConfig(name="generic", url=url, api_key=key), model
        )
        try:
            rows, reachable = api_providers.get_providers(app.session)
            assert "generic" in reachable
            generic = next(r for r in rows if r.id == "generic")
            # The listing is the protocol's: non-empty is the promise. The
            # named model need not appear in it — a catalog's bare listing
            # is not its detailed one, and Ollama lists `name:tag` — the
            # turn below is what proves the model.
            assert generic.models
            app.play("Reply with one word: ready?")
            chain = app.store.stories.get_messages(app.session.story_id)
            assert chain[1].role == "assistant"
            assert chain[1].body.strip()
        finally:
            app.close()
