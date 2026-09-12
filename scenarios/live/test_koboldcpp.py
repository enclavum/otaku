"""KoboldCpp smokes — one small model per process;
scripts/live-providers.sh launches it. Marked `live`; they skip
themselves when nothing listens on the default port.
"""

from pathlib import Path

import pytest

from otaku.backend.api import providers as api_providers
from otaku.settings.providers import ProviderConfig
from scenarios.support.live import first_model
from scenarios.support.live import live_app as build_app

URL = "http://127.0.0.1:5001/v1"

pytestmark = pytest.mark.live


class TestKoboldCpp:
    def test_a_turn_streams_and_persists(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Reply with one word: ready?")
        chain = live_app.store.stories.get_messages(live_app.session.story_id)
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()

    def test_the_context_window_reads_from_the_extra_api(self, live_app) -> None:  # type: ignore[no-untyped-def]
        rows, _ = api_providers.get_providers(live_app.session)
        provider = next(r for r in rows if r.id == "koboldcpp")
        row = next(m for m in provider.models if m.name == live_app.session.model)
        assert row.max_context_loaded  # the /api/extra window rode the listing


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    # KoboldCpp's raw /v1 lists its model as "koboldcpp/NAME"; the app's
    # own listing strips that brand, so the smoke plays the model under
    # the name the picker would use.
    model = first_model(URL).removeprefix("koboldcpp/")
    app = build_app(tmp_path, server, ProviderConfig(name="koboldcpp", url=URL), model)
    yield app
    app.close()
