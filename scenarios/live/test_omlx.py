"""oMLX smokes — the server is assumed to be running with a model
loaded. Marked `live`; they skip themselves when it isn't listening or
nothing is loaded. The url and api key come from omlx's own settings
file (`autoconfigure` — the same detection a first run performs), so a
non-default port just works; OTAKU_LIVE_OMLX_URL overrides it, and
OTAKU_LIVE_OMLX_MODEL overrides the model (default: the loaded one).
"""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from otaku.providers.clients.omlx import OmlxClient
from scenarios.support.live import live_app as build_app

_AUTO = OmlxClient.autoconfigure()
URL = os.environ.get("OTAKU_LIVE_OMLX_URL", _AUTO.url)
MODEL = os.environ.get("OTAKU_LIVE_OMLX_MODEL", "")

pytestmark = pytest.mark.live


class TestOmlx:
    def test_a_turn_streams_and_persists(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Reply with one word: ready?")
        chain = live_app.store.stories.get_messages(live_app.session.story_id)
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()

    def test_the_listing_marks_the_loaded_model(self, live_app) -> None:  # type: ignore[no-untyped-def]
        client = live_app.session.providers.get_client("omlx")
        rows = client.models()
        assert rows
        assert any(row.loaded for row in rows)


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    provider_config = replace(_AUTO, url=URL)
    try:
        rows = OmlxClient(provider_config).models(timeout=3.0)
    except Exception:
        pytest.skip(f"no server at {URL}")
    loaded = [row for row in rows if row.loaded]
    if not loaded and not MODEL:
        pytest.skip("no model loaded in omlx")
    app = build_app(tmp_path, server, provider_config, MODEL or loaded[0].name)
    yield app
    app.close()
