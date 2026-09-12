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
    KeySource,
    ModelInfo,
    OpenAIClient,
    ProviderConfig,
    ProviderError,
    UnreachableError,
)

VARIABLE = "OTAKU_TEST_ENGINE_KEY"


class Engine(OpenAIClient):
    """The plainest engine: class knowledge alone, and a variable."""

    kind = "engine"
    label = "Engine"
    env_key = VARIABLE


class Mute(OpenAIClient):
    """An engine that names no variable."""

    kind = "mute"
    label = "Mute"


class TestApiKey:
    def test_the_sections_key_is_in_force(self, monkeypatch) -> None:
        monkeypatch.setenv(VARIABLE, "from-env")
        client = Engine(ProviderConfig(name="engine", url="u", api_key="typed"))
        assert client.api_key == "typed"
        assert client.key_source is KeySource.CONFIG

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
    def test_what_an_engine_could_not_read_is_none_not_a_guess(self) -> None:
        assert Capabilities() == Capabilities(vision=None, thinking=None, text_completion=None)
        assert Capabilities(vision=False).thinking is None

    def test_a_row_carries_none_until_a_listing_reads_them(self) -> None:
        assert ModelInfo(name="m").capabilities is None


class Sized(OpenAIClient):
    """An engine whose context source answers a script, and counts; its
    one-model ask knows every name."""

    kind = "sized"
    label = "Sized"

    def __init__(self, *answers: int | Exception | None) -> None:
        super().__init__(ProviderConfig(name="sized", url="u"))
        self.answers = list(answers)
        self.asked = 0

    def _model(self, name: str, timeout: float) -> ModelInfo | None:
        return ModelInfo(name=name)

    def _context_size(self, model: str) -> int | None:
        self.asked += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class TestContextSize:
    def test_a_real_answer_is_written_into_the_model_and_kept(self) -> None:
        client = Sized(4096)
        assert _window(client, "m") == 4096
        assert _window(client, "m") == 4096
        assert client.asked == 1

    def test_an_unknown_is_asked_again(self) -> None:
        # The usual cause is asking before the model loads.
        client = Sized(None, 8192)
        assert _window(client, "m") is None
        assert _window(client, "m") == 8192
        assert client.asked == 2

    def test_a_source_that_raised_is_left_alone_for_the_session(self) -> None:
        # A catalog that is down must not tax every turn with a timeout.
        client = Sized(UnreachableError("down"), 4096)
        assert _window(client, "m") is None
        assert _window(client, "m") is None
        assert client.asked == 1

    def test_the_window_is_per_model(self) -> None:
        client = Sized(1024, 2048)
        assert _window(client, "a") == 1024
        assert _window(client, "b") == 2048


class Listing(OpenAIClient):
    kind = "listing"
    label = "Listing"

    def __init__(self, rows: list[ModelInfo] | Exception) -> None:
        super().__init__(ProviderConfig(name="listing", url="u"))
        self.rows = rows

    def _list_models(self, timeout: float) -> list[ModelInfo]:
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


class TestOneModel:
    def test_the_model_is_found_by_name_and_checked(self) -> None:
        # Asked once, and the engine could say nothing: checked and silent.
        client = Listing([ModelInfo(name="a"), ModelInfo(name="b", max_context_loaded=42)])
        assert client.model("b") == ModelInfo(
            name="b", max_context_loaded=42, capabilities=Capabilities()
        )
        assert client.model("c") is None

    def test_a_provider_that_will_not_answer_is_none_never_an_error(self) -> None:
        assert Listing(UnreachableError("down")).model("a") is None


class Remembering(OpenAIClient):
    """An engine whose listing carries one complete row and one the
    listing could not complete, whose one-model ask completes any name
    it is given, and which counts every ask."""

    kind = "remembering"
    label = "Remembering"

    def __init__(self) -> None:
        super().__init__(ProviderConfig(name="remembering", url="u"))
        self.listed = 0
        self.asked_one = 0
        self.sized = 0
        self.loaded: set[str] = set()

    def _list_models(self, timeout: float) -> list[ModelInfo]:
        self.listed += 1
        seen = Capabilities(vision=True, thinking=frozenset(), text_completion=True)
        return [
            ModelInfo(
                name="whole",
                max_context_loaded=4096,
                capabilities=seen,
                loaded="whole" in self.loaded,
            ),
            ModelInfo(name="bare", loaded="bare" in self.loaded),
        ]

    def _model(self, name: str, timeout: float) -> ModelInfo | None:
        self.asked_one += 1
        if name == "ghost":
            return None
        return ModelInfo(name=name, capabilities=Capabilities(vision=False), loaded=True)

    def _context_size(self, model: str) -> int | None:
        self.sized += 1
        return 2048

    def _load_model(self, model: str) -> None:
        self.loaded.add(model)

    def _unload_model(self, model: str) -> None:
        self.loaded.discard(model)


class TestModels:
    def test_a_listing_is_asked_every_time_and_refreshes_the_models(self) -> None:
        client = Remembering()
        client.models()
        client.loaded.add("whole")
        client.models()
        assert client.listed == 2
        assert client.model("whole") is not None and client.model("whole").loaded is True

    def test_a_listing_that_cannot_read_capabilities_keeps_what_the_ask_read(self) -> None:
        # The one-model ask's word is static; a listing that carries none
        # must not blank it — or /info would forget Vision after the
        # picker opened.
        client = Remembering()
        client.models()
        asked = client.model("bare")
        client.models()
        assert client.model("bare") is not None
        assert client.model("bare").capabilities == Capabilities(vision=False)
        assert asked is not None and client.asked_one == 1
        assert next(r for r in client.models() if r.name == "bare").capabilities is not None

    def test_a_complete_model_is_served_without_asking(self) -> None:
        client = Remembering()
        client.models()
        assert client.model("whole") == client.models()[0]
        assert client.asked_one == 0

    def test_a_model_the_listing_could_not_complete_is_asked_once(self) -> None:
        client = Remembering()
        client.models()
        first = client.model("bare")
        assert first is not None and first.capabilities == Capabilities(vision=False)
        assert client.model("bare") == first
        assert client.asked_one == 1

    def test_a_name_nobody_offers_is_asked_again_and_stays_none(self) -> None:
        client = Remembering()
        assert client.model("ghost") is None
        assert client.model("ghost") is None
        assert client.asked_one == 2

    def test_the_context_size_is_the_models_and_a_real_answer_is_written_into_it(self) -> None:
        client = Remembering()
        client.models()
        assert _window(client, "whole") == 4096  # the listing's word
        assert _window(client, "bare") == 2048  # asked once, then the model's own
        assert _window(client, "bare") == 2048
        assert client.sized == 1
        assert client.model("bare") is not None
        assert client.model("bare").max_context_loaded == 2048

    def test_a_load_or_unload_settles_the_state_and_keeps_the_rest(self) -> None:
        # What changed is known exactly: the loaded state, and a context
        # size that is the new instance's and so unknown again. The
        # capabilities stay, and nothing is asked of the engine for it.
        client = Remembering()
        client.models()
        assert _window(client, "bare") == 2048
        client.load_model("bare")
        loaded = client.model("bare")
        assert loaded is not None and loaded.loaded is True
        assert loaded.capabilities == Capabilities(vision=False) and client.sized == 2
        client.unload_model("bare")
        unloaded = client.model("bare")
        assert unloaded is not None and unloaded.loaded is False
        assert client.asked_one == 1


class TestBaseAnswers:
    def test_the_base_manages_nothing_and_refuses_a_load(self) -> None:
        client = Engine(ProviderConfig(name="engine", url="u"))
        assert client.can_manage_models is False
        with pytest.raises(ProviderError):
            client.load_model("m")
        with pytest.raises(ProviderError):
            client.unload_model("m")

    def test_the_base_has_no_account_and_counts_nothing(self) -> None:
        client = Engine(ProviderConfig(name="engine", url="u"))
        assert client.balance() is None
        assert client.can_count_tokens is False
        assert client.count_chat_tokens("m", []) is None
        assert client.count_text_tokens("m", "p") is None

    def test_the_base_sends_the_effort_on_chat_and_nothing_on_text(self) -> None:
        assert OpenAIClient.thinking_knobs == frozenset({"reasoning_effort"})
        assert OpenAIClient.text_thinking_knobs == frozenset()
        assert OpenAIClient.can_mark_cache is False


def _window(client: OpenAIClient, name: str) -> int | None:
    """The loaded context size as a reader gets it: off the model."""
    found = client.model(name)
    return found.max_context_loaded if found else None
