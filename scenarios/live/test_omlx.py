"""oMLX smokes — the server is assumed to be running with a model
loaded. Marked `live`; they skip themselves when it isn't listening or
nothing is loaded. The url and api key come from omlx's own settings
file (`autoconfigure` — the same detection a first run performs), so a
non-default port just works; OTAKU_LIVE_OMLX_URL overrides it, and
OTAKU_LIVE_OMLX_MODEL overrides the model (default: the loaded one).
The dictation smoke writes down a sentence spoken by this machine's
voice with OTAKU_LIVE_OMLX_DICTATION_MODEL, default the first speech-to-
text model it serves (whisper-large-v3-turbo, parakeet); it skips where
no dictation model is offered.
"""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from otaku.providers import ModelState
from otaku.providers.clients.omlx import OmlxClient
from scenarios.support.live import assert_written_down, dictation_model, spoken_wav
from scenarios.support.live import live_app as build_app

_AUTO = OmlxClient.autoconfigure()
URL = os.environ.get("OTAKU_LIVE_OMLX_URL", _AUTO.url)
MODEL = os.environ.get("OTAKU_LIVE_OMLX_MODEL", "")
DICTATION_MODEL = os.environ.get("OTAKU_LIVE_OMLX_DICTATION_MODEL", "")

pytestmark = pytest.mark.live


class TestOmlx:
    def test_a_turn_streams_and_persists(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Reply with one word: ready?")
        chain = live_app.store.stories.get_messages(live_app.story_id)
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()

    def test_the_listing_marks_the_loaded_model(self, live_app) -> None:  # type: ignore[no-untyped-def]
        providers = live_app.api.providers.list().providers
        provider = next(r for r in providers if r.id == "omlx")
        assert provider.models
        assert any(row.state is ModelState.LOADED for row in provider.models)

    def test_a_recording_is_written_down(self, live_app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        model = dictation_model(live_app, "omlx", DICTATION_MODEL)
        assert_written_down(live_app, "omlx", model, spoken_wav(tmp_path))


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    provider_config = replace(_AUTO, url=URL)
    try:
        rows = OmlxClient(provider_config).models.list(timeout=3.0)
    except Exception:
        pytest.skip(f"no server at {URL}")
    loaded = [row for row in rows if row.state is ModelState.LOADED]
    if not loaded and not MODEL:
        pytest.skip("no model loaded in omlx")
    app = build_app(tmp_path, server, provider_config, MODEL or loaded[0].name)
    yield app
    app.close()
