"""NanoGPT smokes — the real catalog, the account balance, and one
short prompt to a popular cheap model (a fraction of a cent). Marked
`live`; they skip themselves when NANOGPT_API_KEY is not set. The model
comes from OTAKU_LIVE_NANOGPT_MODEL, default gpt-4o-mini.
The dictation smoke writes down a sentence spoken by this machine's
voice with OTAKU_LIVE_NANOGPT_DICTATION_MODEL, default Whisper-Large-V3;
it skips where no dictation model is offered.
"""

import os
from pathlib import Path

import pytest

from otaku.providers.clients.nanogpt import NanoGptClient
from otaku.settings.providers import ProviderConfig
from scenarios.support.live import assert_written_down, dictation_model, require_env, spoken_wav
from scenarios.support.live import live_app as build_app

URL = "https://nano-gpt.com/api/v1"
MODEL = os.environ.get("OTAKU_LIVE_NANOGPT_MODEL", "gpt-4o-mini")
DICTATION_MODEL = os.environ.get("OTAKU_LIVE_NANOGPT_DICTATION_MODEL", "Whisper-Large-V3")

pytestmark = pytest.mark.live


class TestNanoGpt:
    def test_the_catalog_lists_with_context_windows(self, client: NanoGptClient) -> None:
        rows = client.models.list()
        assert rows
        assert any(row.context_length for row in rows)

    def test_the_balance_reads_in_dollars(self, client: NanoGptClient) -> None:
        money = client.balance()
        assert money is not None
        assert money.currency == "USD"
        assert str(money).startswith("$")

    def test_a_short_prompt_answers(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Reply with the single word: ok")
        chain = live_app.store.stories.get_messages(live_app.story_id)
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()

    def test_a_recording_is_written_down(self, live_app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        model = dictation_model(live_app, "nanogpt", DICTATION_MODEL)
        assert_written_down(live_app, "nanogpt", model, spoken_wav(tmp_path))


@pytest.fixture
def client() -> NanoGptClient:
    key = require_env("NANOGPT_API_KEY")
    return NanoGptClient(ProviderConfig(name="nanogpt", url=URL, api_key=key))


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    key = require_env("NANOGPT_API_KEY")
    provider_config = ProviderConfig(name="nanogpt", url=URL, api_key=key)
    app = build_app(tmp_path, server, provider_config, MODEL)
    yield app
    app.close()
