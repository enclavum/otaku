"""LM Studio smokes — the headless server (`lms server start`); a chat
auto-loads its model just in time, so nothing needs preloading. Marked
`live`; they skip themselves when nothing listens on the default port.
OTAKU_LIVE_LMSTUDIO_MODEL overrides the model; by default the first
listed one plays. LMSTUDIO_API_KEY carries the API token LM Studio
requires by default since 0.4.16 — leave it unset when the requirement
is disabled in the app.
"""

import os
from pathlib import Path

import pytest

from otaku.settings.config import ProviderConfig
from scenarios.support.live import first_model
from scenarios.support.live import live_app as build_app

URL = "http://127.0.0.1:1234/v1"
MODEL = os.environ.get("OTAKU_LIVE_LMSTUDIO_MODEL", "")
KEY = os.environ.get("LMSTUDIO_API_KEY", "")

pytestmark = pytest.mark.live


class TestLmStudio:
    def test_a_turn_streams_and_persists(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Reply with one word: ready?")
        chain = live_app.store.stories.get_messages(live_app.session.story_id)
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()

    def test_the_registry_lists_rich_rows(self, live_app) -> None:  # type: ignore[no-untyped-def]
        client = live_app.session.providers.get_client("lmstudio")
        rows = client.models()
        assert rows
        assert all(row.name for row in rows)


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    model = MODEL or first_model(URL, KEY)
    app = build_app(tmp_path, server, ProviderConfig(name="lmstudio", url=URL, api_key=KEY), model)
    yield app
    app.close()
