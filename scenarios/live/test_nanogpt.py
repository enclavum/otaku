"""NanoGPT smokes — the real catalog, the account balance, and one
short prompt to a popular cheap model (a fraction of a cent). Marked
`live`; they skip themselves when NANOGPT_API_KEY is not set. The model
comes from OTAKU_LIVE_NANOGPT_MODEL, default gpt-4o-mini.
"""

import os
from pathlib import Path

import pytest

from otaku.providers.backends.nanogpt import NanoGptClient
from otaku.settings.config import Provider
from scenarios.support.live import live_app as build_app
from scenarios.support.live import require_env

URL = "https://nano-gpt.com/api/v1"
MODEL = os.environ.get("OTAKU_LIVE_NANOGPT_MODEL", "gpt-4o-mini")

pytestmark = pytest.mark.live


class TestNanoGpt:
    def test_the_catalog_lists_with_context_windows(self, client: NanoGptClient) -> None:
        rows = client.models()
        assert rows
        assert any(row.context for row in rows)

    def test_the_balance_reads_in_dollars(self, client: NanoGptClient) -> None:
        value = client.balance()
        assert value is not None
        assert value.startswith("$")

    def test_a_short_prompt_answers(self, live_app) -> None:  # type: ignore[no-untyped-def]
        live_app.play("Reply with the single word: ok")
        chain = live_app.store.stories.get_messages(live_app.session.story_id)
        assert chain[1].role == "assistant"
        assert chain[1].body.strip()


@pytest.fixture
def client() -> NanoGptClient:
    key = require_env("NANOGPT_API_KEY")
    return NanoGptClient(Provider(name="nanogpt", url=URL, api_key=key))


@pytest.fixture
def live_app(tmp_path: Path, server):  # type: ignore[no-untyped-def]
    key = require_env("NANOGPT_API_KEY")
    provider = Provider(name="nanogpt", url=URL, api_key=key)
    app = build_app(tmp_path, server, provider, MODEL)
    yield app
    app.close()
