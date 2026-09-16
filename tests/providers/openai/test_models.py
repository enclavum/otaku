"""The model half's pure facts: what a request gets for its context, the
capability defaults, and what the base half answers before an engine."""

import pytest

from otaku.providers import ModelCapabilities, ModelInfo, ModelState, ProviderConfig, ProviderError
from otaku.providers.http import Http
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.models import OpenAIModels

CONFIG = ProviderConfig(name="engine", url="http://127.0.0.1:9/v1")


def _half() -> OpenAIModels:
    return OpenAIModels(CONFIG, OpenAIAuth(CONFIG, ""), Http("engine", {}))


class TestMaxContext:
    def test_the_loaded_size_is_what_a_request_gets(self) -> None:
        row = ModelInfo("m", max_context_catalogue=131_072, max_context_loaded=8192)
        assert row.max_context == 8192

    @pytest.mark.parametrize("state", list(ModelState))
    def test_the_loaded_size_wins_in_every_state(self, state: ModelState) -> None:
        assert ModelInfo("m", max_context_loaded=4096, state=state).max_context == 4096

    def test_the_models_own_serves_where_loading_is_not_a_thing(self) -> None:
        catalog = ModelInfo("m", max_context_catalogue=128_000, state=ModelState.UNKNOWN)
        assert catalog.max_context == 128_000

    @pytest.mark.parametrize("state", [ModelState.UNLOADED, ModelState.LOADING, ModelState.LOADED])
    def test_an_engines_model_without_a_loaded_size_has_none(self, state: ModelState) -> None:
        assert ModelInfo("m", max_context_catalogue=131_072, state=state).max_context is None

    def test_nothing_known_is_none(self) -> None:
        assert ModelInfo("m").max_context is None


class TestDefaults:
    def test_a_row_says_nothing_until_told(self) -> None:
        row = ModelInfo("m")
        assert (row.size, row.max_context_catalogue, row.max_context_loaded) == (None, None, None)
        assert (row.max_output_tokens, row.capabilities, row.state) == (
            None,
            None,
            ModelState.UNKNOWN,
        )

    def test_capabilities_are_unknown_until_stated(self) -> None:
        caps = ModelCapabilities()
        assert (caps.vision, caps.audio) == (None, None)
        thinking = (caps.reasoning_efforts, caps.reasoning_switch, caps.reasoning_budget)
        assert thinking == (None, None, None)
        assert (caps.text_completion, caps.structured_output) == (None, None)

    def test_rows_and_capabilities_are_values(self) -> None:
        assert ModelInfo("m", size=1) == ModelInfo("m", size=1)
        assert ModelCapabilities(vision=True) == ModelCapabilities(vision=True)
        with pytest.raises(AttributeError):
            ModelInfo("m").size = 2  # type: ignore[misc]


class TestTheBaseHalf:
    def test_manages_nothing_and_says_so(self) -> None:
        half = _half()
        assert half.can_manage is False
        with pytest.raises(ProviderError, match="cannot be loaded or unloaded on engine"):
            half.load("m")
        with pytest.raises(ProviderError, match="cannot be loaded or unloaded on engine"):
            half.unload("m")

    def test_remembers_nothing_before_a_listing(self) -> None:
        assert _half().cached("m") is None

    def test_a_listing_is_public_by_default(self) -> None:
        assert OpenAIModels.listing_keyed is False
        assert OpenAIModels.listing_query == ""
