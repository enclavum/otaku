"""The completion half's class knowledge and its pure values: what the
base promises of an engine's wire before an engine says otherwise."""

from otaku.providers import ProviderConfig, Stats
from otaku.providers.http import Http
from otaku.providers.openai.completion import PROTOCOL_PARAMS, SAMPLER_PARAMS, OpenAICompletion
from otaku.providers.openai.reasoning import EFFORT_KNOB

CONFIG = ProviderConfig(name="engine", url="http://127.0.0.1:9/v1")


class TestParameters:
    def test_the_protocols_own_are_the_seven_every_endpoint_reads(self) -> None:
        assert (
            frozenset(
                {
                    "temperature",
                    "top_p",
                    "max_tokens",
                    "presence_penalty",
                    "frequency_penalty",
                    "seed",
                    "stop",
                }
            )
            == PROTOCOL_PARAMS
        )

    def test_the_samplers_are_the_three_beyond_it(self) -> None:
        assert frozenset({"top_k", "min_p", "repetition_penalty"}) == SAMPLER_PARAMS
        assert not PROTOCOL_PARAMS & SAMPLER_PARAMS

    def test_the_base_reads_the_protocols_own(self) -> None:
        assert OpenAICompletion.supported_params == PROTOCOL_PARAMS


class TestClassKnowledge:
    def test_the_base_sends_the_effort_by_name_on_chat_and_nothing_on_text(self) -> None:
        assert OpenAICompletion.chat_reasoning_knobs == frozenset({EFFORT_KNOB})
        assert OpenAICompletion.text_reasoning_knobs == frozenset()

    def test_the_base_counts_nothing_and_marks_no_cache(self) -> None:
        assert OpenAICompletion.can_count_tokens is False
        assert OpenAICompletion.can_mark_cache is False
        half = OpenAICompletion(CONFIG, Http("engine", {}), request_sink=None, smooth=False)
        assert half.count_chat_tokens("m", []) is None
        assert half.count_text_tokens("m", "raw") is None


class TestStats:
    def test_nothing_is_known_until_the_stream_says(self) -> None:
        stats = Stats()
        assert (stats.prompt_tokens, stats.completion_tokens, stats.cached_tokens) == (None,) * 3
        assert stats.first_token_seconds is None
        assert stats.total_seconds == 0.0
