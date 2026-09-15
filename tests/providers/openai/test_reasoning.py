"""The reasoning vocabulary and how an effort is spelled on each knob an
engine reads."""

import pytest

from otaku.providers import reasoning
from otaku.providers.openai.reasoning import (
    ALL_EFFORTS,
    EFFORT_KNOB,
    EFFORTS,
    FLAG_KNOB,
    TEMPLATE_EFFORT_KNOB,
    fields,
    from_wire,
)


class TestVocabulary:
    def test_the_words_run_from_off_to_strongest(self) -> None:
        assert EFFORTS == ("none", "minimal", "low", "medium", "high", "xhigh", "max")

    def test_all_efforts_is_the_whole_vocabulary(self) -> None:
        assert frozenset(EFFORTS) == ALL_EFFORTS

    def test_the_module_is_reachable_off_the_package(self) -> None:
        assert reasoning.EFFORTS is EFFORTS


class TestFields:
    def test_no_effort_sends_nothing(self) -> None:
        assert fields(None, frozenset({EFFORT_KNOB, FLAG_KNOB, TEMPLATE_EFFORT_KNOB})) == {}

    def test_no_knob_sends_nothing(self) -> None:
        assert fields("high", frozenset()) == {}

    def test_a_word_outside_the_vocabulary_cannot_travel(self) -> None:
        with pytest.raises(ValueError):
            fields("bogus", frozenset({EFFORT_KNOB}))

    def test_the_effort_knob_carries_the_word(self) -> None:
        assert fields("high", frozenset({EFFORT_KNOB})) == {"reasoning_effort": "high"}
        assert fields("none", frozenset({EFFORT_KNOB})) == {"reasoning_effort": "none"}

    def test_the_flag_knob_is_the_templates_switch(self) -> None:
        assert fields("none", frozenset({FLAG_KNOB})) == {
            "chat_template_kwargs": {"enable_thinking": False}
        }
        assert fields("low", frozenset({FLAG_KNOB})) == {
            "chat_template_kwargs": {"enable_thinking": True}
        }

    def test_the_template_knob_carries_the_word_beside_the_flag_never_for_none(self) -> None:
        both = frozenset({FLAG_KNOB, TEMPLATE_EFFORT_KNOB})
        assert fields("high", both) == {
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "high"}
        }
        assert fields("none", both) == {"chat_template_kwargs": {"enable_thinking": False}}
        assert fields("none", frozenset({TEMPLATE_EFFORT_KNOB})) == {}

    def test_every_knob_at_once_nests_the_template_ones(self) -> None:
        assert fields("medium", frozenset({EFFORT_KNOB, FLAG_KNOB, TEMPLATE_EFFORT_KNOB})) == {
            "reasoning_effort": "medium",
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "medium"},
        }


class TestFromWire:
    def test_keeps_the_words_we_spell_and_drops_the_rest(self) -> None:
        assert from_wire(["low", "bogus", 3, None, "max", "low"]) == frozenset({"low", "max"})

    def test_nothing_named_is_the_empty_set(self) -> None:
        assert from_wire([]) == frozenset()
