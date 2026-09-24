"""How a turn looks, where it can be checked without a terminal: the
command vocabulary the highlighter picks by; the blocks a reply's calls
and the thinking are drawn as — behind the bar, wrapped at the width by
the terminal's own rule, the same whole or in pieces; the question
parted from its options as the tool reads them; what `message` makes of
a reply; and the two readings off the story — whether notes are shown,
and the question it stands on."""

import io
from types import SimpleNamespace

from prompt_toolkit.utils import get_cwidth

from otaku.backend.story import StorySettingAssistantNotes
from otaku.backend.tools import ToolQuestions
from otaku.store.schema import Message
from otaku.terminal.tty import DIM
from otaku.terminal.tty.render import (
    BAR,
    BlockStream,
    QuestionStream,
    asked,
    block,
    command_tokens,
    message,
    notes_displayed,
    spaced,
)

NORMAL = "\x1b[22m"  # intensity back to normal: what closes a dim block


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
    from its options as the tool reads them (`ToolQuestions`), line by
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
            assert words == " ".join(ToolQuestions(text).question.split()), text

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
    goes on; a note only while `notes`."""

    def test_a_question_is_its_question_alone_as_a_block(self) -> None:
        out = message(
            "The door creaks.<otk-question>Go in?\n1. Yes\n2. No</otk-question>", "assistant"
        )
        assert out == f"The door creaks.\n\n{BAR}Go in?"

    def test_a_note_is_dim_while_the_story_displays_notes_and_gone_otherwise(self) -> None:
        body = "The door creaks.<otk-note>Keep the key.</otk-note>"
        assert (
            message(body, "assistant", notes=True)
            == f"The door creaks.\n\n{DIM}{BAR}Keep the key.{NORMAL}"
        )
        assert message(body, "assistant", notes=False) == "The door creaks."

    def test_prose_after_a_block_is_set_apart_by_one_blank_line(self) -> None:
        body = "<otk-note>Key.</otk-note>\n\n\nThen prose."
        assert message(body, "assistant", notes=True) == f"{DIM}{BAR}Key.{NORMAL}\n\nThen prose."

    def test_a_block_wraps_at_the_width_given(self) -> None:
        body = (
            "<otk-question>Does Mara confess tonight, or wait for the ball?\n1. Yes</otk-question>"
        )
        rows = message(body, "assistant", width=30).split("\n")
        assert len(rows) > 1 and all(row.startswith(BAR) and _columns(row) <= 30 for row in rows)


class TestNotesDisplayed:
    """`notes_displayed` — the page's `displayTools` rule copied: the
    notes tool on, with its display switch on."""

    def test_reads_the_two_facts(self) -> None:
        for enabled, display, shown in (
            (True, True, True),
            (True, False, False),
            (False, True, False),
        ):
            settings = FakeSettings(enabled=enabled, display_notes=display)
            assert notes_displayed(settings) is shown, (enabled, display)  # type: ignore[arg-type]

    def test_a_build_without_the_tool_shows_nothing(self) -> None:
        assert notes_displayed(FakeSettings(missing=True)) is False  # type: ignore[arg-type]


class TestAsked:
    """`asked` — the question the story stands on, read off the newest
    message: a reply whose last call is a question, nothing after it but
    notes and whitespace."""

    def test_a_reply_ending_on_a_question(self) -> None:
        found = asked(reply("The door.<otk-question>Go in?\n1. Yes\n2. No</otk-question>"))
        assert found is not None and (found.question, found.options) == ("Go in?", ("Yes", "No"))

    def test_notes_and_whitespace_after_it_do_not_answer_it(self) -> None:
        body = "<otk-question>Go in?\n1. Yes</otk-question>\n<otk-note>Key.</otk-note>\n\n"
        assert asked(reply(body)) is not None

    def test_prose_after_it_means_the_model_went_on(self) -> None:
        assert asked(reply("<otk-question>Go in?\n1. Yes</otk-question>It opens.")) is None

    def test_a_line_of_the_readers_or_no_message_means_none(self) -> None:
        assert asked(Message(role="user", body="Yes")) is None
        assert asked(None) is None


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


def _columns(row: str) -> int:
    return sum(max(0, get_cwidth(ch)) for ch in row.replace(DIM, "").replace(NORMAL, ""))


class FakeSettings:
    """A story's settings with the notes tool as asked, or without it."""

    def __init__(self, *, enabled: bool = False, display_notes: bool = True, missing: bool = False):
        self._notes = (
            None if missing else SimpleNamespace(enabled=enabled, display_notes=display_notes)
        )

    def get(self, name: str) -> object:
        return self._notes if name == StorySettingAssistantNotes.name else None
