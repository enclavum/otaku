"""The providers package against every real engine, one matrix: what
only a live server can prove, asserted where the outcome is the
package's promise and left to the report script where it is the
model's own appetite (`scripts/live-matrix.py`).

Per provider, on the model its case names: the one-model row and its
capabilities decoded; a turn at "off" with no thinking echo where the
model can be switched off, and at "high" with the level on the wire; a
text completion answering as text; the cat photo to a model that takes
images, answered with the word; the token counts where the provider
counts; the balance where there is an account. Marked `live`; a case
skips itself when its server is down or its key is not set. Same knobs
as the other smokes: the local engines via scripts/live-providers.sh,
the catalogs via OPENROUTER_API_KEY and NANOGPT_API_KEY, LM Studio via
LMSTUDIO_API_KEY, a model per provider via OTAKU_LIVE_<PROVIDER>_MODEL.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from otaku.providers import (
    ALL_CLIENTS,
    Image,
    Locality,
    OpenAIClient,
    ProviderConfig,
    Reasoning,
    Text,
)
from otaku.providers.clients.omlx import OmlxClient
from scenarios.support.live import case_key, case_model

pytestmark = pytest.mark.live

CASES = [
    ("llamacpp", "http://127.0.0.1:8080/v1", "", os.environ.get("OTAKU_LIVE_LLAMACPP_MODEL", "")),
    ("koboldcpp", "http://127.0.0.1:5001/v1", "", os.environ.get("OTAKU_LIVE_KOBOLDCPP_MODEL", "")),
    (
        "ollama",
        "http://127.0.0.1:11434/v1",
        "",
        os.environ.get("OTAKU_TEST_MODEL", "ollama/gemma3").partition("/")[2],
    ),
    (
        "omlx",
        os.environ.get("OTAKU_LIVE_OMLX_URL", OmlxClient.autoconfigure().url),
        "",
        os.environ.get("OTAKU_LIVE_OMLX_MODEL", ""),
    ),
    (
        "lmstudio",
        "http://127.0.0.1:1234/v1",
        "LMSTUDIO_API_KEY",
        os.environ.get("OTAKU_LIVE_LMSTUDIO_MODEL", ""),
    ),
    (
        "openrouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        os.environ.get("OTAKU_LIVE_OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it"),
    ),
    (
        "nanogpt",
        "https://nano-gpt.com/api/v1",
        "NANOGPT_API_KEY",
        os.environ.get("OTAKU_LIVE_NANOGPT_MODEL", "google/gemma-4-26b-a4b-it"),
    ),
]
_REQUIRED_KEYS = {"OPENROUTER_API_KEY", "NANOGPT_API_KEY"}
_IDS = [c[0] for c in CASES]

# A question a model may think about.
PUZZLE = (
    "A farmer has 17 sheep. All but 9 run away. Then he buys twice as many "
    "as remain. How many sheep now? Give only the number."
)
# A real photograph of a cat (a young one), downsized to 640px. A model
# that sees it says what it is — either word.
CAT = Path(__file__).parent.parent / "fixtures" / "cat.jpg"


@dataclass(frozen=True)
class Turn:
    role: str
    body: str


@pytest.fixture(params=CASES, ids=_IDS)
def case(request):  # type: ignore[no-untyped-def]
    """One provider's client and model, or a skip with the reason."""
    provider, url, key_var, model = request.param
    key = case_key(provider, key_var, _REQUIRED_KEYS)
    model = case_model(provider, url, key, model)
    client = ALL_CLIENTS[provider](ProviderConfig(name=provider, url=url, api_key=key))
    return client, model


class TestMatrix:
    def test_the_one_model_row_is_read(self, case) -> None:  # type: ignore[no-untyped-def]
        client, model = case
        row = client.models.get(model)
        assert row is not None and row.name == model
        # A provider that reports capabilities decodes them; the generic
        # provider is the one that reports nothing.
        if client.id != "generic":
            assert row.capabilities is not None

    def test_none_stops_reasoning_and_an_effort_rides_the_wire(self, case) -> None:  # type: ignore[no-untyped-def]
        client, model = case
        row = client.models.get(model)
        reasoning, text = _turn(client, model, PUZZLE, "none")
        assert text.strip()
        honoured = row.capabilities.reasoning if row and row.capabilities else None
        if honoured and "none" in honoured:
            assert reasoning == "", "the model reasoned although none was sent"
        _, text = _turn(client, model, PUZZLE, "high")
        assert text.strip()

    def test_a_text_completion_answers_as_text(self, case) -> None:  # type: ignore[no-untyped-def]
        client, model = case
        chunks = list(
            client.completion.text(
                model,
                "The capital of France is",
                {"max_tokens": 8, "temperature": 0},
                watched=False,
            )
        )
        assert "".join(c.text for c in chunks if isinstance(c, Text)).strip()
        assert not any(isinstance(c, Reasoning) for c in chunks)

    def test_a_vision_model_sees_the_cat(self, case) -> None:  # type: ignore[no-untyped-def]
        client, model = case
        row = client.models.get(model)
        if row is None or row.capabilities is None or not row.capabilities.vision:
            pytest.skip(f"{model} does not take images, or its provider cannot say")
        messages = [
            Turn("system", "Answer with one word."),
            Turn("user", "What animal is this?"),
        ]
        chunks = client.completion.chat(
            model,
            messages,
            {"max_tokens": 200, "temperature": 0},
            effort="none",
            images=[Image(CAT.read_bytes(), "image/jpeg")],
            watched=False,
        )
        answer = "".join(c.text for c in chunks if isinstance(c, Text)).lower()
        assert "cat" in answer or "kitten" in answer, answer

    def test_the_counts_come_where_the_engine_counts(self, case) -> None:  # type: ignore[no-untyped-def]
        client, model = case
        messages = [Turn("system", "s"), Turn("user", "Hello there.")]
        chat, text = (
            client.completion.count_chat_tokens(model, messages),
            client.completion.count_text_tokens(model, "Hello"),
        )
        if not client.completion.can_count_tokens:
            assert chat is None and text is None
            return
        # llama.cpp's chat count needs a build with the endpoint; its raw
        # count and the others' counts are always there.
        if client.id == "llamacpp":
            assert isinstance(text, int) and text > 0
            assert chat is None or chat > 0
        elif client.id == "koboldcpp":
            assert isinstance(chat, int) and isinstance(text, int)
        elif client.id == "omlx":
            assert isinstance(chat, int) and text is None

    def test_a_catalog_has_a_balance_and_a_local_engine_has_none(self, case) -> None:  # type: ignore[no-untyped-def]
        client, _ = case
        money = client.balance(timeout=10.0)
        if client.locality is Locality.REMOTE:
            assert money is not None and money.currency == "USD"
        else:
            assert money is None


def _turn(client: OpenAIClient, model: str, prompt: str, effort: str) -> tuple[str, str]:
    """One turn at `effort`: (reasoning, text) as they streamed."""
    messages = [Turn("system", "You are a careful assistant."), Turn("user", prompt)]
    reasoning, text = [], []
    for chunk in client.completion.chat(
        model, messages, {"max_tokens": 800, "temperature": 0}, effort=effort, watched=False
    ):
        if isinstance(chunk, Reasoning):
            reasoning.append(chunk.text)
        elif isinstance(chunk, Text):
            text.append(chunk.text)
    return "".join(reasoning), "".join(text)
