"""llama.cpp smokes — `llama-server` fronting one small model;
scripts/live-providers.sh launches it. Marked `live`; they skip
themselves when nothing listens on the default port.
The dictation smoke writes down a sentence spoken by this machine's
voice with OTAKU_LIVE_LLAMACPP_DICTATION_MODEL, default its own model
where it takes audio (Gemma 4 E4B with its projector); it skips where no
dictation model is offered.
"""

import os
from pathlib import Path

import pytest

from otaku.settings.providers import ProviderConfig
from scenarios.support.live import assert_written_down, dictation_model, first_model, spoken_wav
from scenarios.support.live import live_app as build_app

URL = "http://127.0.0.1:8080/v1"
DICTATION_MODEL = os.environ.get("OTAKU_LIVE_LLAMACPP_DICTATION_MODEL", "")

pytestmark = pytest.mark.live


class TestLlamaCpp:
    def test_a_turn_streams_and_persists(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Reply with one word: ready?")
        chain = live_app.store.stories.get_messages(live_app.story_id)
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()

    def test_the_context_window_reads_from_props(self, live_app) -> None:  # type: ignore[no-untyped-def]
        rows = live_app.api.providers.list().providers
        provider = next(r for r in rows if r.id == "llamacpp")
        assert any(m.context_loaded for m in provider.models)

    def test_a_recording_is_written_down(self, live_app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        model = dictation_model(live_app, "llamacpp", DICTATION_MODEL)
        assert_written_down(live_app, "llamacpp", model, spoken_wav(tmp_path))


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    model = first_model(URL)
    app = build_app(tmp_path, server, ProviderConfig(name="llamacpp", url=URL), model)
    yield app
    app.close()
