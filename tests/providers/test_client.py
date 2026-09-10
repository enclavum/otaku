"""The base client's promises that need no server.

The key in force is the section's, else the environment variable the
engine names, else none, and `key_source` says which; an engine that
names no variable never reads the environment. A model's capabilities
default to allowed. The context size is cached only when real: an
unknown is asked again, a source that raised is left alone for the
session. `model` never raises. Every hook the base declares has a base
answer: no listing beyond ids, no load or unload, no balance, no
counts, no text knob.
"""

import pytest

from otaku.providers import (
    Capabilities,
    Client,
    KeySource,
    ModelInfo,
    ProviderConfig,
    ProviderError,
    UnreachableError,
)
from otaku.providers.wire import ALL_THINKING_LEVELS

VARIABLE = "OTAKU_TEST_ENGINE_KEY"


class Engine(Client):
    """The plainest engine: class knowledge alone, and a variable."""

    kind = "engine"
    label = "Engine"
    env_key = VARIABLE


class Mute(Client):
    """An engine that names no variable."""

    kind = "mute"
    label = "Mute"


class TestApiKey:
    def test_the_sections_key_is_in_force(self, monkeypatch) -> None:
        monkeypatch.setenv(VARIABLE, "from-env")
        client = Engine(ProviderConfig(name="engine", url="u", api_key="typed"))
        assert client.api_key == "typed"
        assert client.key_source is KeySource.SECTION

    def test_the_variable_stands_in_for_an_empty_section(self, monkeypatch) -> None:
        monkeypatch.setenv(VARIABLE, "from-env")
        client = Engine(ProviderConfig(name="engine", url="u"))
        assert client.api_key == "from-env"
        assert client.key_source is KeySource.ENV

    def test_no_key_anywhere_is_none(self, monkeypatch) -> None:
        monkeypatch.delenv(VARIABLE, raising=False)
        client = Engine(ProviderConfig(name="engine", url="u"))
        assert client.api_key == ""
        assert client.key_source is None

    def test_an_engine_without_a_variable_never_reads_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv(VARIABLE, "from-env")
        assert Mute(ProviderConfig(name="mute", url="u")).key_source is None
        assert Mute.env_api_key() == ""

    def test_the_class_answers_for_its_variable(self, monkeypatch) -> None:
        monkeypatch.setenv(VARIABLE, "from-env")
        assert Engine.env_api_key() == "from-env"
        monkeypatch.delenv(VARIABLE)
        assert Engine.env_api_key() == ""

    def test_autoconfigure_writes_no_key(self, monkeypatch) -> None:
        # The variable is read at request time, never into a section.
        monkeypatch.setenv(VARIABLE, "from-env")
        assert Engine.autoconfigure() == ProviderConfig(name="engine", url="")


class TestCapabilities:
    def test_unknown_reads_as_allowed(self) -> None:
        assert Capabilities() == Capabilities(
            vision=True, thinking=ALL_THINKING_LEVELS, completion=True
        )

    def test_a_row_carries_none_until_a_listing_reads_them(self) -> None:
        assert ModelInfo(name="m").capabilities is None


class Sized(Client):
    """An engine whose context source answers a script, and counts."""

    kind = "sized"
    label = "Sized"

    def __init__(self, *answers: int | Exception | None) -> None:
        super().__init__(ProviderConfig(name="sized", url="u"))
        self.answers = list(answers)
        self.asked = 0

    def _context_size(self, model: str) -> int | None:
        self.asked += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class TestContextSize:
    def test_a_real_answer_is_cached(self) -> None:
        client = Sized(4096)
        assert client.get_context_size("m") == 4096
        assert client.get_context_size("m") == 4096
        assert client.asked == 1

    def test_an_unknown_is_asked_again(self) -> None:
        # The usual cause is asking before the model loads.
        client = Sized(None, 8192)
        assert client.get_context_size("m") is None
        assert client.get_context_size("m") == 8192
        assert client.asked == 2

    def test_a_source_that_raised_is_left_alone_for_the_session(self) -> None:
        # A catalog that is down must not tax every turn with a timeout.
        client = Sized(UnreachableError("down"), 4096)
        assert client.get_context_size("m") is None
        assert client.get_context_size("m") is None
        assert client.asked == 1

    def test_the_cache_is_per_model(self) -> None:
        client = Sized(1024, 2048)
        assert client.get_context_size("a") == 1024
        assert client.get_context_size("b") == 2048


class Listing(Client):
    kind = "listing"
    label = "Listing"

    def __init__(self, rows: list[ModelInfo] | Exception) -> None:
        super().__init__(ProviderConfig(name="listing", url="u"))
        self.rows = rows

    def models(self, timeout: float = 10.0) -> list[ModelInfo]:
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


class TestOneModel:
    def test_the_row_is_found_by_name(self) -> None:
        client = Listing([ModelInfo(name="a"), ModelInfo(name="b", context=42)])
        assert client.model("b") == ModelInfo(name="b", context=42)
        assert client.model("c") is None

    def test_a_provider_that_will_not_answer_is_none_never_an_error(self) -> None:
        assert Listing(UnreachableError("down")).model("a") is None


class TestBaseAnswers:
    def test_the_base_manages_nothing_and_refuses_a_load(self) -> None:
        client = Engine(ProviderConfig(name="engine", url="u"))
        assert client.manages_models is False
        with pytest.raises(ProviderError):
            client.load_model("m")
        with pytest.raises(ProviderError):
            client.unload_model("m")

    def test_the_base_has_no_account_and_counts_nothing(self) -> None:
        client = Engine(ProviderConfig(name="engine", url="u"))
        assert client.balance() is None
        assert client.counts_tokens is False
        assert client.count_chat_tokens("m", []) is None
        assert client.count_text_tokens("m", "p") is None

    def test_the_base_sends_the_effort_on_chat_and_nothing_on_text(self) -> None:
        assert Client.thinking_knobs == frozenset({"reasoning_effort"})
        assert Client.text_thinking_knobs == frozenset()
        assert Client.cache_markers is False
