"""The tools a model may use by writing a call in its reply.

A tool is declared by its class — its name, who answers its call, the
prompts key of its prompt — and registered under its name; `read` binds
one call to the tool its name says, in any case. `ToolQuestions` reads
its call as a question and the answers to pick from: the question is
everything before the first answer line; an answer line opens `1.`,
`1)`, `a.` or `a)`; none makes the question free-form; whatever follows
the answers that is not one is no answer; nine at most. `to_json` is
the call as a frontend draws it; `to_prose` what a call reads as on the
wire while its tool is off — the question alone, nothing for a note.
"""

import re
from dataclasses import fields

import pytest

from otaku.backend.tools import TOOLS, Actor, Tool, ToolAssistantNotes, ToolQuestions, read
from otaku.settings.prompts import Prompts


class TestAsk:
    def test_the_question_and_its_numbered_answers(self) -> None:
        ask = ToolQuestions("\nStay at the inn, or go?\n1. She stays\n2. She goes\n")
        assert ask.question == "Stay at the inn, or go?"
        assert ask.options == ("She stays", "She goes")

    def test_a_question_without_answers_is_free_form(self) -> None:
        ask = ToolQuestions("What is her brother's name?")
        assert ask.question == "What is her brother's name?"
        assert ask.options == ()

    def test_answers_may_be_lettered_or_closed_with_a_bracket(self) -> None:
        ask = ToolQuestions("Stay or go?\na) She stays\nB. She goes\n3) She hides")
        assert ask.options == ("She stays", "She goes", "She hides")

    def test_the_question_may_run_over_lines(self) -> None:
        ask = ToolQuestions("The bridge is out.\nWhich way now?\n1. Upstream\n2. Back")
        assert ask.question == "The bridge is out.\nWhich way now?"

    def test_a_line_after_the_answers_is_no_answer(self) -> None:
        # A picked answer is SENT as the reader's line: a closing remark
        # must not ride it.
        ask = ToolQuestions("Stay or go?\n1. She stays\n2. She goes\nChoose wisely!")
        assert ask.options == ("She stays", "She goes")

    def test_blank_lines_are_passed_over(self) -> None:
        ask = ToolQuestions("Stay or go?\n\n1. She stays\n\n2. She goes")
        assert ask.question == "Stay or go?"
        assert ask.options == ("She stays", "She goes")

    def test_a_number_inside_a_sentence_opens_no_answer(self) -> None:
        ask = ToolQuestions("Is it chapter 1. or chapter 2?")
        assert ask.question == "Is it chapter 1. or chapter 2?"
        assert ask.options == ()

    def test_nine_answers_at_most(self) -> None:
        ask = ToolQuestions("Which?\n" + "\n".join(f"{n}. Door {n}" for n in range(1, 13)))
        assert ask.options == tuple(f"Door {n}" for n in range(1, 10))

    def test_the_call_is_kept_as_written(self) -> None:
        text = "\nStay or go?\n1. She stays\n"
        assert ToolQuestions(text).text == text

    def test_to_json_carries_the_tool_and_what_it_read(self) -> None:
        assert ToolQuestions("Stay or go?\n1. Stay").to_json() == {
            "tool": "question",
            "text": "Stay or go?\n1. Stay",
            "question": "Stay or go?",
            "options": ["Stay"],
        }
        assert ToolAssistantNotes("the seal").to_json() == {"tool": "note", "text": "the seal"}

    def test_off_a_question_reads_as_the_question_alone_and_a_note_as_nothing(self) -> None:
        # The options were the menu's — a model no longer offered the tool
        # must not learn one; the question still gives the answer that
        # follows something to answer.
        assert ToolQuestions.to_prose("Stay or go?\n1. Stay\n2. Go") == "Stay or go?"
        assert ToolAssistantNotes.to_prose("the seal") == ""


class TestIsOption:
    """`ToolQuestions.is_option` — the rule the parse parts a question
    from its options by, for a reader of the call as it streams."""

    def test_an_option_is_a_head_then_the_answer(self) -> None:
        for line in ("1. Yes", "2) No", "a. Yes", "B) No", "12. Twelve", "  3. Indented"):
            assert ToolQuestions.is_option(line), line

    def test_a_head_alone_or_glued_to_its_text_is_not_one(self) -> None:
        for line in ("1.", "1.Yes", "1", "Yes", "", "A question?", "10x. No", "123. Too long"):
            assert not ToolQuestions.is_option(line), line

    def test_agrees_with_the_parse(self) -> None:
        text = "Go in?\n1. Yes\n2. No\nA remark.\n3. Wait"
        lines = text.splitlines()
        question = "\n".join(line for line in lines if not ToolQuestions.is_option(line))
        # the parse keeps the lines before the first option as the question
        assert ToolQuestions(text).question == "Go in?"
        assert question.startswith("Go in?")
        assert [line for line in lines if ToolQuestions.is_option(line)] == [
            "1. Yes",
            "2. No",
            "3. Wait",
        ]


class TestRead:
    def test_a_call_is_read_as_the_tool_its_name_says(self) -> None:
        ask = read("question", "Stay or go?\n1. Stay")
        assert isinstance(ask, ToolQuestions) and ask.options == ("Stay",)
        assert isinstance(read("note", "the letter is forged"), ToolAssistantNotes)

    def test_the_name_is_read_in_any_case(self) -> None:
        assert isinstance(read("NOTE", "x"), ToolAssistantNotes)

    def test_a_name_no_tool_owns_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            read("plan", "x")


class TestRegistry:
    def test_every_tool_is_registered_under_its_own_name(self) -> None:
        # Holds a forgotten registration AND a name two tools claim: the
        # registry keeps one class per name, so one of the two would miss.
        for tool in every_tool(Tool):
            assert TOOLS.get(tool.name) is tool, tool.__name__

    def test_a_name_is_one_lowercase_word(self) -> None:
        for name in TOOLS:
            assert re.fullmatch(r"[a-z]+", name), name

    def test_every_tool_names_a_prompt_the_prompts_hold(self) -> None:
        held = {field.name for field in fields(Prompts)}
        for tool in TOOLS.values():
            assert tool.prompt_name in held, tool.__name__

    def test_a_call_has_its_actor_and_an_aside_has_none(self) -> None:
        assert ToolQuestions.actor is Actor.USER
        assert ToolAssistantNotes.actor is Actor.NOBODY


def every_tool(base: type[Tool]) -> list[type[Tool]]:
    """Every class below `base`, however deep."""
    found: list[type[Tool]] = []
    for sub in base.__subclasses__():
        found += [sub, *every_tool(sub)]
    return found
