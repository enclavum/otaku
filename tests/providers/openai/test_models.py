"""The model half's pure facts: what a request gets for its context, the
capability defaults, what the base half answers before an engine, and
the kinds an entry is filed under — the listing's word, completion where
it says none, dictation besides for a model that takes audio where the
engine's dictation runs one — kept across a read that states none."""

import pytest

from otaku.providers import (
    ModelCapabilities,
    ModelInfo,
    ModelKind,
    ModelState,
    ProviderConfig,
    ProviderError,
)
from otaku.providers.http import Http
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.models import OpenAIModels

CONFIG = ProviderConfig(name="engine", url="http://127.0.0.1:9/v1")


def _half(models_class: type[OpenAIModels] = OpenAIModels) -> OpenAIModels:
    return models_class(CONFIG, OpenAIAuth(CONFIG, ""), Http("engine", {}))


class _Hearing(OpenAIModels):
    """An engine whose dictation runs a completion model that takes audio."""

    dictation_models_separated = False


BOTH = frozenset({ModelKind.COMPLETION, ModelKind.DICTATION})
HEARS = ModelCapabilities(audio=True)


class TestContextWindow:
    def test_the_loaded_size_is_what_a_request_gets(self) -> None:
        row = ModelInfo("m", context_length=131_072, context_loaded=8192)
        assert row.context_window == 8192

    @pytest.mark.parametrize("state", list(ModelState))
    def test_the_loaded_size_wins_in_every_state(self, state: ModelState) -> None:
        assert ModelInfo("m", context_loaded=4096, state=state).context_window == 4096

    def test_the_models_own_serves_where_loading_is_not_a_thing(self) -> None:
        catalog = ModelInfo("m", context_length=128_000, state=ModelState.UNKNOWN)
        assert catalog.context_window == 128_000

    @pytest.mark.parametrize("state", [ModelState.UNLOADED, ModelState.LOADING, ModelState.LOADED])
    def test_an_engines_model_without_a_loaded_size_has_none(self, state: ModelState) -> None:
        assert ModelInfo("m", context_length=131_072, state=state).context_window is None

    def test_nothing_known_is_none(self) -> None:
        assert ModelInfo("m").context_window is None


class TestDefaults:
    def test_a_row_says_nothing_until_told(self) -> None:
        row = ModelInfo("m")
        assert (row.size, row.context_length, row.context_loaded) == (None, None, None)
        assert (row.max_output_tokens, row.capabilities, row.state) == (
            None,
            None,
            ModelState.UNKNOWN,
        )

    def test_capabilities_are_unknown_until_stated(self) -> None:
        caps = ModelCapabilities()
        assert (caps.vision, caps.audio, caps.supported_params) == (None, None, None)
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
        assert OpenAIModels.completion_query == ""

    def test_lists_no_dictation_models_apart_and_keeps_them_apart(self) -> None:
        assert OpenAIModels.dictation_query == ""
        assert OpenAIModels.dictation_models_separated is True


class TestKinds:
    def test_a_row_states_no_kind_until_told(self) -> None:
        assert ModelInfo("m").kinds == frozenset()

    def test_a_row_that_states_none_is_a_completion_model(self) -> None:
        assert _half()._settle_kinds(ModelInfo("m")).kinds == {ModelKind.COMPLETION}

    def test_the_listings_word_stands(self) -> None:
        stated = ModelInfo("m", kinds=frozenset({ModelKind.DICTATION}))
        assert _half()._settle_kinds(stated).kinds == {ModelKind.DICTATION}

    def test_a_model_that_takes_audio_dictates_where_the_engines_dictation_runs_one(
        self,
    ) -> None:
        assert _half(_Hearing)._settle_kinds(ModelInfo("m", capabilities=HEARS)).kinds == BOTH

    def test_where_dictation_models_stand_apart_taking_audio_is_not_enough(self) -> None:
        settled = _half()._settle_kinds(ModelInfo("m", capabilities=HEARS))
        assert settled.kinds == {ModelKind.COMPLETION}

    @pytest.mark.parametrize("audio", [False, None])
    def test_a_model_that_does_not_or_may_not_take_audio_does_not_dictate(
        self, audio: bool | None
    ) -> None:
        row = ModelInfo("m", capabilities=ModelCapabilities(audio=audio))
        assert _half(_Hearing)._settle_kinds(row).kinds == {ModelKind.COMPLETION}

    def test_a_read_that_states_no_kind_keeps_the_listings(self) -> None:
        cached = ModelInfo("m", kinds=BOTH)
        assert _half()._merge(cached, ModelInfo("m")).kinds == BOTH

    def test_a_read_that_states_kinds_replaces_them(self) -> None:
        cached = ModelInfo("m", kinds=BOTH)
        fresh = ModelInfo("m", kinds=frozenset({ModelKind.COMPLETION}))
        assert _half()._merge(cached, fresh).kinds == {ModelKind.COMPLETION}
