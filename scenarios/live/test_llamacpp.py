"""llama.cpp smokes — `llama-server` fronting one small model;
scripts/live-providers.sh launches it. Marked `live`; they skip
themselves when nothing listens on the default port.
"""

from pathlib import Path

import pytest

from otaku.settings.config import Provider
from scenarios.support.live import first_model
from scenarios.support.live import live_app as build_app

URL = "http://127.0.0.1:8080/v1"

pytestmark = pytest.mark.live


class TestLlamaCpp:
    def test_a_turn_streams_and_persists(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Reply with one word: ready?")
        chain = live_app.store.stories.get_messages(live_app.session.story_id)
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()

    def test_the_context_window_reads_from_props(self, live_app) -> None:  # type: ignore[no-untyped-def]
        client = live_app.session.providers.get_client("llamacpp")
        assert client.get_context_size(live_app.session.model)


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    model = first_model(URL)
    app = build_app(tmp_path, server, Provider(name="llamacpp", url=URL), model)
    yield app
    app.close()
