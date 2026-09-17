"""The thinking vocabulary — the ladder, the switch, a budget — and how
a level is spelled on each knob an engine reads."""

import pytest

from otaku.providers import reasoning
from otaku.providers.openai.reasoning import (
    ALL_EFFORT_LEVELS,
    BUDGET_KNOB,
    BUDGET_MAX_TOKENS_KNOB,
    BUDGET_TOKENS_KNOB,
    EFFORT_KNOB,
    EFFORT_LEVELS,
    EFFORT_TEMPLATE_KNOB,
    SWITCH_LEVELS,
    SWITCH_TEMPLATE_KNOB,
    budget_of,
    fields,
    from_wire,
    is_level,
)

EVERY_KNOB = frozenset(
    {
        BUDGET_KNOB,
        BUDGET_MAX_TOKENS_KNOB,
        BUDGET_TOKENS_KNOB,
        EFFORT_KNOB,
        EFFORT_TEMPLATE_KNOB,
        SWITCH_TEMPLATE_KNOB,
    }
)


class TestVocabulary:
    def test_the_words_run_from_off_to_strongest(self) -> None:
        assert EFFORT_LEVELS == ("none", "minimal", "low", "medium", "high", "xhigh", "max")

    def test_all_efforts_is_the_whole_ladder(self) -> None:
        assert frozenset(EFFORT_LEVELS) == ALL_EFFORT_LEVELS

    def test_the_switch_has_two_positions(self) -> None:
        assert SWITCH_LEVELS == ("off", "on")

    def test_a_budget_is_digits_and_zero_is_one(self) -> None:
        assert budget_of("2000") == 2000
        assert budget_of("0") == 0
        assert budget_of("low") is None
        assert budget_of("-1") is None
        assert budget_of("1.5") is None
        assert budget_of("") is None

    def test_a_level_is_a_rung_a_position_or_a_budget(self) -> None:
        assert all(is_level(word) for word in (*EFFORT_LEVELS, *SWITCH_LEVELS, "0", "12"))
        assert not any(is_level(word) for word in ("bogus", "-1", "1.5", "", "unset"))

    def test_the_module_is_reachable_off_the_package(self) -> None:
        assert reasoning.EFFORT_LEVELS is EFFORT_LEVELS


class TestFields:
    def test_no_level_sends_nothing(self) -> None:
        assert fields(None, EVERY_KNOB) == {}

    def test_no_knob_sends_nothing(self) -> None:
        assert fields("high", frozenset()) == {}

    def test_a_word_outside_the_vocabulary_cannot_travel(self) -> None:
        for word in ("bogus", "-1", "1.5", "unset"):
            with pytest.raises(ValueError):
                fields(word, frozenset({EFFORT_KNOB}))

    def test_the_effort_knob_carries_the_rung(self) -> None:
        assert fields("high", frozenset({EFFORT_KNOB})) == {"reasoning_effort": "high"}
        assert fields("none", frozenset({EFFORT_KNOB})) == {"reasoning_effort": "none"}

    def test_the_flag_knob_is_the_templates_switch(self) -> None:
        assert fields("none", frozenset({SWITCH_TEMPLATE_KNOB})) == {
            "chat_template_kwargs": {"enable_thinking": False}
        }
        assert fields("low", frozenset({SWITCH_TEMPLATE_KNOB})) == {
            "chat_template_kwargs": {"enable_thinking": True}
        }

    def test_the_template_knob_carries_the_rung_beside_the_flag_none_included(self) -> None:
        # A template that reads no flag turns off on the word, so none
        # travels on the variable too.
        both = frozenset({SWITCH_TEMPLATE_KNOB, EFFORT_TEMPLATE_KNOB})
        assert fields("high", both) == {
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "high"}
        }
        assert fields("none", both) == {
            "chat_template_kwargs": {"enable_thinking": False, "reasoning_effort": "none"}
        }
        assert fields("none", frozenset({EFFORT_TEMPLATE_KNOB})) == {
            "chat_template_kwargs": {"reasoning_effort": "none"}
        }

    def test_the_budget_knobs_carry_zero_for_none_and_nothing_for_a_rung(self) -> None:
        # The engine's own sampler stops a thought where no template
        # reads a flag; a rung leaves the budget the engine's default.
        assert fields("none", frozenset({BUDGET_TOKENS_KNOB})) == {"thinking_budget_tokens": 0}
        assert fields("none", frozenset({BUDGET_KNOB})) == {"thinking_budget": 0}
        assert fields("low", frozenset({BUDGET_TOKENS_KNOB, BUDGET_KNOB})) == {}

    def test_off_is_spelled_as_none_on_every_knob(self) -> None:
        assert fields("off", EVERY_KNOB) == fields("none", EVERY_KNOB)
        assert fields("off", EVERY_KNOB) == {
            "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False, "reasoning_effort": "none"},
            "thinking_budget_tokens": 0,
            "thinking_budget": 0,
        }

    def test_on_is_the_flag_alone_and_no_rung(self) -> None:
        assert fields("on", EVERY_KNOB) == {"chat_template_kwargs": {"enable_thinking": True}}
        assert fields("on", frozenset({EFFORT_KNOB})) == {}

    def test_a_budget_is_the_number_with_the_flag_on(self) -> None:
        assert fields("2000", EVERY_KNOB) == {
            "chat_template_kwargs": {"enable_thinking": True},
            "thinking_budget_tokens": 2000,
            "thinking_budget": 2000,
            "reasoning": {"max_tokens": 2000},
        }
        assert fields("2000", frozenset({EFFORT_KNOB})) == {}

    def test_a_budget_of_zero_is_off(self) -> None:
        assert fields("0", EVERY_KNOB) == fields("off", EVERY_KNOB)

    def test_every_knob_at_once_nests_the_template_ones(self) -> None:
        assert fields(
            "medium", frozenset({EFFORT_KNOB, SWITCH_TEMPLATE_KNOB, EFFORT_TEMPLATE_KNOB})
        ) == {
            "reasoning_effort": "medium",
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "medium"},
        }


class TestFromWire:
    def test_keeps_the_words_we_spell_and_drops_the_rest(self) -> None:
        assert from_wire(["low", "bogus", 3, None, "max", "low"]) == frozenset({"low", "max"})

    def test_nothing_named_is_the_empty_set(self) -> None:
        assert from_wire([]) == frozenset()
