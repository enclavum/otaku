"""The tools a model may use by writing a tagged block in its reply.

A tool is declared by its class and registered under its tag; `read`
binds one block to the tool its tag names, in any case. An `Ask` reads
its block as a question and the answers to pick from: the question is
everything before the first answer line; an answer line opens `1.`,
`1)`, `a.` or `a)`; none makes the question free-form; whatever follows
the answers that is not one is no answer; nine at most.
"""

import re

import pytest

from otaku.context.tools import TOOLS, Actor, Ask, Notes, Tool, read


class TestAsk:
    def test_the_question_and_its_numbered_answers(self) -> None:
        ask = Ask("\nStay at the inn, or go?\n1. She stays\n2. She goes\n")
        assert ask.question == "Stay at the inn, or go?"
        assert ask.options == ("She stays", "She goes")

    def test_a_question_without_answers_is_free_form(self) -> None:
        ask = Ask("What is her brother's name?")
        assert ask.question == "What is her brother's name?"
        assert ask.options == ()

    def test_answers_may_be_lettered_or_closed_with_a_bracket(self) -> None:
        ask = Ask("Stay or go?\na) She stays\nB. She goes\n3) She hides")
        assert ask.options == ("She stays", "She goes", "She hides")

    def test_the_question_may_run_over_lines(self) -> None:
        ask = Ask("The bridge is out.\nWhich way now?\n1. Upstream\n2. Back")
        assert ask.question == "The bridge is out.\nWhich way now?"

    def test_a_line_after_the_answers_is_no_answer(self) -> None:
        # A picked answer is SENT as the reader's line: a closing remark
        # must not ride it.
        ask = Ask("Stay or go?\n1. She stays\n2. She goes\nChoose wisely!")
        assert ask.options == ("She stays", "She goes")

    def test_blank_lines_are_passed_over(self) -> None:
        ask = Ask("Stay or go?\n\n1. She stays\n\n2. She goes")
        assert ask.question == "Stay or go?"
        assert ask.options == ("She stays", "She goes")

    def test_a_number_inside_a_sentence_opens_no_answer(self) -> None:
        ask = Ask("Is it chapter 1. or chapter 2?")
        assert ask.question == "Is it chapter 1. or chapter 2?"
        assert ask.options == ()

    def test_nine_answers_at_most(self) -> None:
        ask = Ask("Which?\n" + "\n".join(f"{n}. Door {n}" for n in range(1, 13)))
        assert ask.options == tuple(f"Door {n}" for n in range(1, 10))

    def test_the_block_is_kept_as_written(self) -> None:
        text = "\nStay or go?\n1. She stays\n"
        assert Ask(text).text == text


class TestRead:
    def test_a_block_is_read_as_the_tool_its_tag_names(self) -> None:
        ask = read("ask", "Stay or go?\n1. Stay")
        assert isinstance(ask, Ask) and ask.options == ("Stay",)
        assert isinstance(read("notes", "the letter is forged"), Notes)

    def test_the_tag_is_read_in_any_case(self) -> None:
        assert isinstance(read("NOTES", "x"), Notes)

    def test_a_tag_no_tool_owns_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            read("plan", "x")


class TestRegistry:
    def test_every_tool_is_registered_under_its_own_tag(self) -> None:
        # Holds a forgotten registration AND a tag two tools claim: the
        # registry keeps one class per tag, so one of the two would miss.
        for tool in every_tool(Tool):
            assert TOOLS.get(tool.tag) is tool, tool.__name__

    def test_a_tag_is_one_lowercase_word(self) -> None:
        for tag in TOOLS:
            assert re.fullmatch(r"[a-z]+", tag), tag

    def test_every_tool_names_a_feature_of_its_own(self) -> None:
        names = [tool.feature for tool in TOOLS.values()]
        assert all(names) and len(set(names)) == len(names)

    def test_a_call_has_its_actor_and_an_aside_has_none(self) -> None:
        assert Ask.actor is Actor.USER
        assert Notes.actor is Actor.NOBODY


def every_tool(base: type[Tool]) -> list[type[Tool]]:
    """Every class below `base`, however deep."""
    found: list[type[Tool]] = []
    for sub in base.__subclasses__():
        found += [sub, *every_tool(sub)]
    return found
