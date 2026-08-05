"""Ollama smokes — the plumbing only a live server proves.

Marked `live` (deselect with `-m "not live"`); they skip themselves when
ollama isn't listening or the model isn't pulled. The server is assumed
to be running with its model available; the spec comes from
OTAKU_TEST_MODEL, default ollama/gemma3. A small model's output is not
deterministic, so these assert that otaku behaves — streams, persists,
survives an extraction attempt — not that the model answers well.
"""

import os
from pathlib import Path

import httpx
import pytest

from otaku.settings.config import ProviderConfig
from scenarios.support.live import live_app as build_app

SPEC = os.environ.get("OTAKU_TEST_MODEL", "ollama/gemma3")
URL = "http://127.0.0.1:11434/v1"

pytestmark = pytest.mark.live


class TestOllama:
    def test_a_turn_streams_and_persists(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Ответь одним словом: да или нет?")
        chain = live_app.store.stories.get_messages(live_app.session.story_id)
        assert chain[0].role == "user"
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()

    def test_an_extraction_attempt_never_crashes(self, live_app, capsys) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Меня зовут Кассиан. Я вхожу в тёмную часовню на болоте.")
        live_app.play("Я зажигаю факел и осматриваюсь.")
        live_app.play("/extract")
        out = capsys.readouterr().out
        story_id = live_app.session.story_id
        ids = live_app.store.stories.get_messages_ids(story_id)
        if "closed" in out:  # the model produced parseable JSON
            assert live_app.store.scenes.get_current(story_id, ids)
        else:  # it did not — the tail stays open, the app stays up
            assert "failed" in out or "Nothing new" in out


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    provider_name, _, model = SPEC.partition("/")
    try:
        models = httpx.get(f"{URL}/models", timeout=3.0).json()["data"]
    except Exception:
        pytest.skip(f"no server at {URL}")
    if not any(model in m["id"] for m in models):
        pytest.skip(f"{model!r} is not pulled (ollama pull {model})")
    app = build_app(tmp_path, server, ProviderConfig(name=provider_name, url=URL), model)
    yield app
    app.close()
