"""KoboldCpp smokes — one small model per process;
scripts/live-providers.sh launches it. Marked `live`; they skip
themselves when nothing listens on the default port.
The dictation smoke writes down a sentence spoken by this machine's
voice with OTAKU_LIVE_KOBOLDCPP_DICTATION_MODEL, default its model,
through Whisper (the script loads one beside it) or else the model itself
where it takes audio; it skips where no dictation model is offered.
"""

import os
from pathlib import Path

import pytest

from otaku.settings.providers import ProviderConfig
from scenarios.support.live import assert_written_down, dictation_model, first_model, spoken_wav
from scenarios.support.live import live_app as build_app

URL = "http://127.0.0.1:5001/v1"
DICTATION_MODEL = os.environ.get("OTAKU_LIVE_KOBOLDCPP_DICTATION_MODEL", "")

pytestmark = pytest.mark.live


class TestKoboldCpp:
    def test_a_turn_streams_and_persists(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Reply with one word: ready?")
        chain = live_app.store.stories.get_messages(live_app.story_id)
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()

    def test_the_context_window_reads_from_the_extra_api(self, live_app) -> None:  # type: ignore[no-untyped-def]
        rows = live_app.api.providers.list().providers
        provider = next(r for r in rows if r.id == "koboldcpp")
        row = next(m for m in provider.models if m.name == live_app.api.app.get().model.name)
        assert row.context_loaded  # the /api/extra window rode the listing

    def test_a_recording_is_written_down(self, live_app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        model = dictation_model(live_app, "koboldcpp", DICTATION_MODEL)
        assert_written_down(live_app, "koboldcpp", model, spoken_wav(tmp_path))


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    # KoboldCpp's raw /v1 lists its model as "koboldcpp/NAME"; the app's
    # own listing strips that brand, so the smoke plays the model under
    # the name the picker would use.
    model = first_model(URL).removeprefix("koboldcpp/")
    app = build_app(tmp_path, server, ProviderConfig(name="koboldcpp", url=URL), model)
    yield app
    app.close()
