"""The OpenAI wire as pure functions, request side and answer side.

The vocabulary promises: six thinking levels, "off" first; a level goes
out on each knob the engine reads, spelled the knob's way ("none" for
the effort, a false flag), nothing on no knob and nothing for no level,
and a word outside the vocabulary never travels. The body promises: a
chat request is the messages as role and content, streaming with
usage; the text request is the prompt alone; images ride on the LAST
message as data-url parts; cache marks turn exactly the system row and
the final row into parts, everything between staying plain, and only
the hour TTL is spelled. The answer promises: the usage report is read
in OpenAI's spelling first and Anthropic's as the fallback, a chat
frame yields thinking and text under either name the engines use, a
text frame yields text alone, and a refusal or an error frame is a
sentence, never silence.
"""

import base64
from dataclasses import dataclass

import pytest

from otaku.providers import wire
from otaku.providers.wire import (
    ALL_THINKING_LEVELS,
    THINKING_EFFORT_KNOB,
    THINKING_FLAG_KNOB,
    THINKING_LEVELS,
    Image,
)


@dataclass(frozen=True)
class Turn:
    role: str
    body: str


BOTH = frozenset({THINKING_EFFORT_KNOB, THINKING_FLAG_KNOB})
EFFORT = frozenset({THINKING_EFFORT_KNOB})
FLAG = frozenset({THINKING_FLAG_KNOB})
NONE: frozenset[str] = frozenset()


class TestVocabulary:
    def test_six_levels_off_first_and_weakest_to_strongest(self) -> None:
        assert THINKING_LEVELS == ("off", "low", "medium", "high", "xhigh", "max")
        assert frozenset(THINKING_LEVELS) == ALL_THINKING_LEVELS

    def test_a_catalogs_effort_words_become_levels(self) -> None:
        # OpenAI's "none" is our "off"; the shared words stay; a word we
        # do not spell is dropped, and so is anything not a word.
        words = ["none", "minimal", "low", "high", "xhigh", "max", 3, None]
        assert wire.effort_levels(words) == frozenset({"off", "low", "high", "xhigh", "max"})
        assert wire.effort_levels([]) == frozenset()


class TestThinkingFields:
    def test_the_effort_knob_carries_the_level_and_none_for_off(self) -> None:
        assert wire.thinking_fields("low", EFFORT) == {"reasoning_effort": "low"}
        assert wire.thinking_fields("xhigh", EFFORT) == {"reasoning_effort": "xhigh"}
        assert wire.thinking_fields("off", EFFORT) == {"reasoning_effort": "none"}

    def test_the_template_flag_is_true_for_a_level_and_false_for_off(self) -> None:
        assert wire.thinking_fields("high", FLAG) == {
            "chat_template_kwargs": {"enable_thinking": True}
        }
        assert wire.thinking_fields("off", FLAG) == {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    def test_both_knobs_go_together(self) -> None:
        assert wire.thinking_fields("off", BOTH) == {
            "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False},
        }

    def test_no_level_sends_nothing_whatever_the_knobs(self) -> None:
        assert wire.thinking_fields(None, BOTH) == {}

    def test_no_knob_gets_nothing_whatever_the_level(self) -> None:
        assert wire.thinking_fields("max", NONE) == {}

    def test_a_word_outside_the_vocabulary_never_travels(self) -> None:
        with pytest.raises(ValueError):
            wire.thinking_fields("minimal", EFFORT)
        with pytest.raises(ValueError):
            wire.thinking_fields("none", BOTH)


class TestChatCompletionBody:
    def test_the_messages_stream_with_usage_and_the_params_ride_along(self) -> None:
        body = wire.chat_completion_body(
            "m", [Turn("system", "s"), Turn("user", "u")], {"temperature": 0.7}
        )
        assert body["model"] == "m"
        assert body["messages"] == [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert body["temperature"] == 0.7

    def test_images_ride_on_the_last_message_as_data_url_parts(self) -> None:
        image = Image(b"\x89PNG", "image/png")
        body = wire.chat_completion_body(
            "m", [Turn("user", "before"), Turn("user", "look")], {}, images=[image]
        )
        messages = body["messages"]
        assert isinstance(messages, list)
        assert messages[0] == {"role": "user", "content": "before"}
        encoded = base64.b64encode(b"\x89PNG").decode("ascii")
        assert messages[-1] == {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ],
        }

    def test_no_images_leave_every_message_a_plain_string(self) -> None:
        body = wire.chat_completion_body("m", [Turn("user", "u")], {}, images=[])
        assert body["messages"] == [{"role": "user", "content": "u"}]


class TestCacheMarks:
    def test_system_and_last_are_marked_and_the_middle_stays_plain(self) -> None:
        turns = [Turn("system", "s"), Turn("user", "a"), Turn("assistant", "b"), Turn("user", "c")]
        messages = wire.chat_completion_body("m", turns, {}, cache_ttl="5m")["messages"]
        assert isinstance(messages, list)
        assert messages[0] == {
            "role": "system",
            "content": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}],
        }
        assert messages[1] == {"role": "user", "content": "a"}
        assert messages[2] == {"role": "assistant", "content": "b"}
        assert messages[3] == {
            "role": "user",
            "content": [{"type": "text", "text": "c", "cache_control": {"type": "ephemeral"}}],
        }

    def test_without_a_system_row_only_the_last_is_marked(self) -> None:
        turns = [Turn("user", "a"), Turn("user", "b")]
        messages = wire.chat_completion_body("m", turns, {}, cache_ttl="5m")["messages"]
        assert isinstance(messages, list)
        assert messages[0] == {"role": "user", "content": "a"}
        assert isinstance(messages[1]["content"], list)

    def test_a_single_message_is_marked_once(self) -> None:
        for role in ("user", "system"):
            messages = wire.chat_completion_body("m", [Turn(role, "x")], {}, cache_ttl="5m")[
                "messages"
            ]
            assert isinstance(messages, list)
            assert len(messages) == 1
            parts = messages[0]["content"]
            assert isinstance(parts, list) and len(parts) == 1

    def test_the_hour_ttl_is_spelled_and_the_default_is_not(self) -> None:
        turns = [Turn("user", "x")]
        hour = wire.chat_completion_body("m", turns, {}, cache_ttl="1h")["messages"]
        five = wire.chat_completion_body("m", turns, {}, cache_ttl="5m")["messages"]
        assert isinstance(hour, list) and isinstance(five, list)
        assert hour[0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert five[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_no_ttl_marks_nothing(self) -> None:
        turns = [Turn("system", "s"), Turn("user", "u")]
        messages = wire.chat_completion_body("m", turns, {}, cache_ttl=None)["messages"]
        assert messages == [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

    def test_marks_and_images_share_the_last_message(self) -> None:
        turns = [Turn("user", "look")]
        body = wire.chat_completion_body(
            "m", turns, {}, images=[Image(b"x", "image/jpeg")], cache_ttl="5m"
        )
        parts = body["messages"][0]["content"]  # type: ignore[index]
        assert parts[0]["cache_control"] == {"type": "ephemeral"}
        assert parts[1]["type"] == "image_url"


class TestTextCompletionBody:
    def test_the_prompt_streams_with_usage_and_no_messages(self) -> None:
        body = wire.text_completion_body("m", "Once upon", {"stop": ["\n"]})
        assert body == {
            "model": "m",
            "prompt": "Once upon",
            "stream": True,
            "stream_options": {"include_usage": True},
            "stop": ["\n"],
        }


class TestReadUsage:
    def test_a_frame_without_usage_is_none(self) -> None:
        assert wire.read_usage({"choices": []}) is None

    def test_the_openai_spelling_of_cached_wins(self) -> None:
        usage = {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "prompt_tokens_details": {"cached_tokens": 8},
            "cache_read_input_tokens": 1,
        }
        assert wire.read_usage({"usage": usage}) == (10, 4, 8)

    def test_the_anthropic_spelling_is_the_fallback(self) -> None:
        usage = {"prompt_tokens": 10, "completion_tokens": 4, "cache_read_input_tokens": 3}
        assert wire.read_usage({"usage": usage}) == (10, 4, 3)

    def test_anything_else_is_none_never_a_guess(self) -> None:
        assert wire.read_usage({"usage": {"prompt_tokens": "10"}}) == (None, None, None)
        assert wire.read_usage({"usage": {"prompt_tokens_details": {}}}) == (None, None, None)


class TestFrames:
    def test_a_chat_frame_yields_thinking_under_either_name_and_text(self) -> None:
        assert wire.chat_delta({"choices": [{"delta": {"reasoning_content": "hm"}}]}) == ("hm", "")
        assert wire.chat_delta({"choices": [{"delta": {"reasoning": "hm"}}]}) == ("hm", "")
        assert wire.chat_delta({"choices": [{"delta": {"content": "yes"}}]}) == ("", "yes")
        assert wire.chat_delta({"choices": [{"delta": {"reasoning": "a", "content": "b"}}]}) == (
            "a",
            "b",
        )

    def test_a_frame_without_choices_yields_nothing(self) -> None:
        assert wire.chat_delta({"usage": {}}) == ("", "")
        assert wire.chat_delta({"choices": []}) == ("", "")
        assert wire.completion_delta({}) == ("", "")

    def test_a_text_frame_yields_text_alone(self) -> None:
        assert wire.completion_delta({"choices": [{"text": "more"}]}) == ("", "more")

    def test_an_error_frame_and_a_refusal_are_sentences(self) -> None:
        assert wire.trouble({"error": {"message": "over the limit"}}) == "over the limit"
        assert wire.trouble({"choices": [{"delta": {"refusal": "no"}}]}) == "no"
        assert wire.trouble({"choices": [{"delta": {"content": "fine"}}]}) == ""
        assert wire.trouble({"error": {}}) == ""


class TestPositiveInt:
    def test_only_a_positive_int_passes(self) -> None:
        assert wire.positive_int(5) == 5
        assert wire.positive_int(0) is None
        assert wire.positive_int(-1) is None
        assert wire.positive_int("5") is None
        assert wire.positive_int(None) is None
