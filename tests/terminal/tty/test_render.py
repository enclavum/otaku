"""How a turn looks, where it can be checked without a terminal: the
command vocabulary the highlighter picks by; the blocks a reply's calls
and the thinking are drawn as — behind the bar, wrapped at the width by
the terminal's own rule, the same whole or in pieces; the question
parted from its options as the tool reads them; what `message` makes of
a reply and of a tool's turn; the canvas's echo as one text joined by
the join rule; and the two readings off the story — which tools' calls
are shown, and the question it stands on."""

import io
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace

from prompt_toolkit.utils import get_cwidth

from otaku.backend.story import StorySettingAssistantNotes, StorySettingQuestions
from otaku.backend.tools import ToolQuestion
from otaku.store.schema import Message, StoryMode
from otaku.terminal.tty import DIM
from otaku.terminal.tty.render import (
    BAR,
    BlockStream,
    QuestionStream,
    asked,
    block,
    command_tokens,
    flow,
    flow_offsets,
    last_turns,
    message,
    shown_tools,
    spaced,
    user_block,
)

NORMAL = "\x1b[22m"  # intensity back to normal: what closes a dim block
QUESTIONS = frozenset({"question"})
NOTES = frozenset({"note"})


class TestCommandTokens:
    def test_names_every_command_the_help_lists(self) -> None:
        tokens = command_tokens()
        for command in ("/me", "/you", "/ooc", "/cue", "/undo", "/set", "/info", "/bye"):
            assert command in tokens, command

    def test_holds_each_name_once(self) -> None:
        # `/ooc` is written twice — opening a line, and inside one.
        tokens = command_tokens()
        assert len(tokens) == len(set(tokens))

    def test_leaves_out_what_is_a_key_rather_than_a_command(self) -> None:
        # "Up / Down" carries a bare slash; `@` and '"""' carry none.
        tokens = command_tokens()
        assert "/" not in tokens
        assert all(token.startswith("/") and len(token) > 1 for token in tokens)

    def test_leaves_out_the_argument_placeholders(self) -> None:
        # A label is "/me NAME: PROMPT" — only its command is a token.
        for word in ("PROMPT", "NAME", "TEXT", "FILE", "@"):
            assert word not in command_tokens(), word


class TestSpaced:
    """`spaced` — the newlines that set a call's block apart from what
    was drawn before it: one blank line, however many the model left;
    none for a block that opens the reply."""

    def test_nothing_before_a_block_that_opens_the_reply(self) -> None:
        assert spaced("") == ""
        assert spaced("  \n\n") == ""

    def test_makes_one_blank_line(self) -> None:
        assert spaced("Prose.") == "\n\n"
        assert spaced("Prose.\n") == "\n"
        assert spaced("Prose.\n\n") == ""

    def test_never_takes_back_what_the_model_left(self) -> None:
        assert spaced("Prose.\n\n\n") == ""


class TestBlockStream:
    """`BlockStream` — a call's inside as a block behind the bar, wrapped
    at the width by the terminal's own rule, a word placed once it is
    complete; the echo feeds it whole, the stream in pieces, and the two
    cannot differ."""

    def test_every_row_opens_with_the_bar_and_fits_the_width(self) -> None:
        text = (
            "Keep the key in play. He recognized the seal but said nothing; she has not offered it."
        )
        out = drawn(text, width=40)
        rows = out.split("\n")
        assert len(rows) > 1 and all(row.startswith(BAR) for row in rows)
        assert all(_columns(row) <= 40 for row in rows)
        assert " ".join(row[len(BAR) :] for row in rows) == text

    def test_the_block_ends_its_line_no_more_than_prose_does(self) -> None:
        assert not drawn("Key.", width=40).endswith("\n")

    def test_whitespace_around_the_block_is_dropped_and_runs_made_one(self) -> None:
        assert drawn("  \n Two   words.  \n\n", width=40) == f"{BAR}Two words."

    def test_a_newline_is_a_row_of_its_own_and_a_blank_line_keeps_its_bar(self) -> None:
        assert drawn("One.\n\nTwo.", width=40) == f"{BAR}One.\n{BAR}\n{BAR}Two."

    def test_a_word_wider_than_a_row_is_cut_across_rows(self) -> None:
        out = drawn("Supercalifragilisticexpialidocious!", width=14)
        rows = out.split("\n")
        assert len(rows) == 3 and all(row.startswith(BAR) and _columns(row) <= 14 for row in rows)
        assert "".join(row[len(BAR) :] for row in rows) == "Supercalifragilisticexpialidocious!"

    def test_wide_characters_count_their_columns(self) -> None:
        rows = drawn("日本語の文章です。", width=10).split("\n")
        assert all(_columns(row) <= 10 for row in rows) and len(rows) > 1

    def test_pieces_draw_what_the_whole_draws(self) -> None:
        text = "One two three.\nFour five six seven eight nine ten eleven."
        whole = drawn(text, width=20)
        for size in (1, 2, 3, 7):
            out = io.StringIO()
            stream = BlockStream(out, 20)
            written = stream.begin()
            for i in range(0, len(text), size):
                written += stream.feed(text[i : i + size])
            written += stream.close()
            assert out.getvalue() == whole == written, size

    def test_dim_wraps_the_block_and_nothing_else(self) -> None:
        out = io.StringIO()
        stream = BlockStream(out, 40, dim=True)
        stream.begin()
        stream.feed("Key.")
        stream.close()
        assert out.getvalue().startswith(DIM) and out.getvalue().endswith(NORMAL)
        assert DIM not in drawn("Key.", width=40)

    def test_an_empty_block_draws_nothing(self) -> None:
        assert drawn("   \n ", width=40) == ""

    def test_control_bytes_are_dropped_as_the_typesetter_drops_them(self) -> None:
        # The bytes a terminal could act on go; the printable letters of a
        # would-be escape stay, as `formatting.printable` has it.
        out = drawn("A\x1b[31mB\x07C\x9bD", width=40)
        assert "\x1b" not in out and "\x07" not in out and "\x9b" not in out
        assert out == f"{BAR}A[31mBCD"


class TestQuestionStream:
    """`QuestionStream` — the question alone of a question call, parted
    from its options as the tool reads them (`ToolQuestion`), line by
    line as it streams."""

    def test_draws_what_the_tool_reads_as_the_question(self) -> None:
        for text in (
            "Go in?\n1. Yes\n2. No",
            "\n\nGo in?\n\n1) Yes\n2) No\n",
            "Two lines of question,\nthe second one too.\na. Yes\nb. No",
            "1.\nA bare marker is not an option: this is the question.\n1. Yes\n2. No",
            "Go in?\n1. Yes\nA remark after the options.\n2. No",
            "No options at all, a free question",
            "A. A question that starts like an option is one, as the tool reads it\n1. Yes",
        ):
            shown = asked_block(text, width=200)
            words = " ".join(row[len(BAR) :] for row in shown.split("\n")) if shown else ""
            assert words == " ".join(ToolQuestion(text).question.split()), text

    def test_pieces_draw_what_the_whole_draws(self) -> None:
        text = "Does Mara confess tonight, or wait for the ball?\n1. Tonight\n2. The ball"
        whole = asked_block(text, width=30)
        for size in (1, 2, 5):
            out = io.StringIO()
            stream = QuestionStream(out, 30)
            stream.begin()
            for i in range(0, len(text), size):
                stream.feed(text[i : i + size])
            stream.close()
            assert out.getvalue() == whole, size

    def test_the_question_flows_before_its_options_arrive(self) -> None:
        # A line whose first token is not an option's head streams word
        # by word; nothing waits for the closing tag.
        out = io.StringIO()
        stream = QuestionStream(out, 80)
        stream.begin()
        stream.feed("Does Mara confess ")
        assert out.getvalue() == f"{BAR}Does Mara confess"
        stream.feed("tonight?\n1. ")
        assert out.getvalue() == f"{BAR}Does Mara confess tonight?"
        stream.feed("Yes\n2. No")
        stream.close()
        assert out.getvalue() == f"{BAR}Does Mara confess tonight?"

    def test_a_line_that_may_be_an_option_waits_for_its_end(self) -> None:
        out = io.StringIO()
        stream = QuestionStream(out, 80)
        stream.begin()
        stream.feed("1. ")
        assert out.getvalue() == ""  # held: an option's head, until the newline tells
        stream.feed("\nThe question.")
        stream.close()
        # "1." alone was no option: the tool keeps it as the question's first line
        assert out.getvalue() == f"{BAR}1.\n{BAR}The question."


class TestBlock:
    def test_a_whole_text_as_the_stream_draws_it(self) -> None:
        text = "One two three four five six."
        assert block(text, 16) == drawn(text, width=16)


class TestMessage:
    """`message` over a reply: prose through the typesetter, each call a
    block set apart by a blank line before it and one after where prose
    goes on — each while `shown` holds its tool, left out otherwise."""

    def test_a_question_is_its_question_alone_as_a_block(self) -> None:
        body = "The door creaks.\n```otk-question\nGo in?\n1. Yes\n2. No\n```"
        out = message(body, "assistant", shown=QUESTIONS)
        assert out == f"The door creaks.\n\n{BAR}Go in?"

    def test_a_question_while_its_tool_is_off_is_left_out(self) -> None:
        body = "The door creaks.\n```otk-question\nGo in?\n1. Yes\n```"
        assert message(body, "assistant") == "The door creaks."

    def test_a_note_is_dim_while_the_story_displays_notes_and_gone_otherwise(self) -> None:
        body = "The door creaks.\n```otk-note\nKeep the key.\n```"
        assert (
            message(body, "assistant", shown=NOTES)
            == f"The door creaks.\n\n{DIM}{BAR}Keep the key.{NORMAL}"
        )
        assert message(body, "assistant") == "The door creaks."

    def test_a_call_otaku_runs_draws_nothing(self) -> None:
        body = "Let me look.\n```otk-web-search\ntides\n```"
        assert message(body, "assistant", shown=QUESTIONS | NOTES) == "Let me look."

    def test_prose_after_a_block_is_set_apart_by_one_blank_line(self) -> None:
        body = "```otk-note\nKey.\n```\n\n\nThen prose."
        assert message(body, "assistant", shown=NOTES) == f"{DIM}{BAR}Key.{NORMAL}\n\nThen prose."

    def test_a_block_wraps_at_the_width_given(self) -> None:
        body = "```otk-question\nDoes Mara confess tonight, or wait for the ball?\n1. Yes\n```"
        rows = message(body, "assistant", shown=QUESTIONS, width=30).split("\n")
        assert len(rows) > 1 and all(row.startswith(BAR) and _columns(row) <= 30 for row in rows)


class TestToolTurn:
    """`message` over a tool's turn: the tool's label ALONE as a dim
    block behind the bar — the result is the model's to read, and a
    terminal has no hover to keep it in."""

    def test_the_label_alone_behind_the_bar(self) -> None:
        drawn = message("Time: 12:00\nMoon: full", "tool", label="System info")
        assert drawn == f"{DIM}{BAR}System info{NORMAL}"
        assert "12:00" not in drawn

    def test_a_tool_without_a_label_is_called_a_tool_call(self) -> None:
        assert message("x", "tool") == f"{DIM}{BAR}Tool call{NORMAL}"


class TestFlow:
    """`flow` — the canvas's echo: the turns as ONE text, a user row as
    prose with no block, every row joined to the text before it by the
    join rule (`api.play.join_type`); `flow_offsets` says where each
    turn's own text begins, so an echoed turn can be erased in place."""

    def test_rows_join_inline_or_as_paragraphs_by_the_join_rule(self) -> None:
        rows = [
            story("user", "She waited for"),
            story("assistant", " the bus."),
            story("user", "Then?\n"),
            story("assistant", "Nothing came."),
            story("user", "She shivered."),
        ]
        assert flow(rows) == "She waited for the bus. Then?\n\nNothing came. She shivered."

    def test_chat_rows_are_paragraphs_in_the_one_text(self) -> None:
        rows = [Message(role="user", body="One"), Message(role="assistant", body="Two")]
        assert flow(rows) == "One\n\nTwo"

    def test_a_user_row_is_prose_not_a_block(self) -> None:
        text = flow([Message(role="user", body="I open the door.")])
        assert text == "I open the door."
        assert text != user_block(message("I open the door.", "user"))

    def test_each_turns_text_begins_where_the_offsets_say(self) -> None:
        rows = [
            story("user", "She waited for"),
            story("assistant", "the bus."),
            story("user", "\n\nThen?"),
        ]
        text, starts = flow_offsets(rows)
        assert text == "She waited for the bus.\n\nThen?"
        assert [text[at:] for at in starts] == [text, " the bus.\n\nThen?", "Then?"]
        # a paragraph join's blank goes with the turn BEFORE it
        assert text[starts[2] - 2 : starts[2]] == "\n\n"

    def test_a_tool_turn_draws_nothing_and_the_row_after_it_joins_from_it(self) -> None:
        def rows(make: Callable[[str, str], Message]) -> list[Message]:
            result = replace(make("tool", "Tides: high."), kind="tool", provider="web-search")
            return [make("user", "Look it up."), result, make("assistant", "High at six.")]

        # in story mode the reply goes on in the paragraph the call interrupted
        assert flow(rows(story)) == "Look it up. High at six."
        # chat rows, read in the one text, are paragraphs
        assert flow(rows(chat)) == "Look it up.\n\nHigh at six."

    def test_no_note_rides_the_flow_even_while_displayed(self) -> None:
        rows = [
            Message(role="user", body="One."),
            Message(role="assistant", body="Two.\n```otk-note\nKey.\n```"),
        ]
        assert flow(rows) == "One.\n\nTwo."
        assert flow(rows, shown=NOTES) == "One.\n\nTwo."


class TestLastTurns:
    """`last_turns` — the resume echo: the blocks of the last turns, or
    on the canvas the one text they are."""

    def test_the_blocks_one_blank_line_between(self) -> None:
        rows = [
            Message(role="user", body="One."),
            Message(role="assistant", body="Two."),
            Message(role="user", body="Three."),
        ]
        assert last_turns(rows, 2) == "Two.\n\n" + user_block(message("Three.", "user"))

    def test_on_the_canvas_the_one_text(self) -> None:
        rows = [story("user", "She waited for"), story("assistant", "the bus.")]
        assert last_turns(rows, 2, is_story_mode=True) == "She waited for the bus."
        assert last_turns(rows, 1, is_story_mode=True) == "the bus."


class TestShownTools:
    """`shown_tools` — the page's `displayTools` rule copied: a question
    while its tool is on, the notes while theirs is on with its display
    switch."""

    def test_reads_the_facts_the_settings_report(self) -> None:
        for questions, notes, display, shown in (
            (True, False, True, {"question"}),
            (False, True, True, {"note"}),
            (False, True, False, set()),
            (False, False, True, set()),
            (True, True, True, {"question", "note"}),
        ):
            settings = FakeSettings(questions=questions, notes=notes, display_notes=display)
            assert shown_tools(settings) == shown, (questions, notes, display)  # type: ignore[arg-type]

    def test_a_build_without_the_tools_shows_nothing(self) -> None:
        assert shown_tools(FakeSettings(missing=True)) == frozenset()  # type: ignore[arg-type]


class TestAsked:
    """`asked` — the question the story stands on, read off the newest
    message: a reply whose last call is a question, nothing after it but
    notes and whitespace."""

    def test_a_reply_ending_on_a_question(self) -> None:
        body = "The door.\n```otk-question\nGo in?\n1. Yes\n2. No\n```"
        found = asked(reply(body), QUESTIONS)
        assert found is not None and (found.question, found.options) == ("Go in?", ("Yes", "No"))

    def test_none_while_the_questions_tool_is_off(self) -> None:
        assert asked(reply("```otk-question\nGo in?\n1. Yes\n```"), frozenset()) is None

    def test_notes_and_whitespace_after_it_do_not_answer_it(self) -> None:
        body = "```otk-question\nGo in?\n1. Yes\n```\n```otk-note\nKey.\n```\n\n"
        assert asked(reply(body), QUESTIONS) is not None

    def test_prose_after_it_means_the_model_went_on(self) -> None:
        body = "```otk-question\nGo in?\n1. Yes\n```\nIt opens."
        assert asked(reply(body), QUESTIONS) is None

    def test_a_line_of_the_readers_or_no_message_means_none(self) -> None:
        assert asked(Message(role="user", body="Yes"), QUESTIONS) is None
        assert asked(None, QUESTIONS) is None


def drawn(text: str, *, width: int) -> str:
    """`text` as a block, fed whole."""
    out = io.StringIO()
    stream = BlockStream(out, width)
    stream.begin()
    stream.feed(text)
    stream.close()
    return out.getvalue()


def asked_block(text: str, *, width: int) -> str:
    out = io.StringIO()
    stream = QuestionStream(out, width)
    stream.begin()
    stream.feed(text)
    stream.close()
    return out.getvalue()


def reply(body: str) -> Message:
    return Message(role="assistant", body=body)


def story(role: str, body: str) -> Message:
    """A row written in story mode."""
    return Message(role=role, body=body, mode=StoryMode.STORY)


def chat(role: str, body: str) -> Message:
    """A row written in chat mode, as every row before story mode was."""
    return Message(role=role, body=body)


def _columns(row: str) -> int:
    return sum(max(0, get_cwidth(ch)) for ch in row.replace(DIM, "").replace(NORMAL, ""))


class FakeSettings:
    """A story's settings with the questions and notes tools as asked, or
    without them."""

    def __init__(
        self,
        *,
        questions: bool = False,
        notes: bool = False,
        display_notes: bool = True,
        missing: bool = False,
    ):
        self._by_name: dict[str, object] = (
            {}
            if missing
            else {
                StorySettingQuestions.name: SimpleNamespace(enabled=questions),
                StorySettingAssistantNotes.name: SimpleNamespace(
                    enabled=notes, display_notes=display_notes
                ),
            }
        )

    def get(self, name: str) -> object:
        return self._by_name.get(name)
