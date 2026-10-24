"""The terminal's screen, played turn by turn in a pty and read back
through an emulated terminal (`support.emulator`): after every step the
screen shows what a fresh launch echoes of the same story — the ledger's
erases and redraws leave nothing behind and take nothing they should not
— and while a reply streams, no finished row above it moves. Story mode
is compared word for word, its one text wrapping wherever its echo began;
the rows the store never holds (the thinking, an error, the verbose
stats) are left out of the comparison.

Minutes, not seconds: every check launches a second app for its echo. So
the module is `slow` besides `cli`, and runs only when asked for with
`--slow` (`make regression`).
"""

import contextlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from otaku.backend.api import Layout
from scenarios.support.emulator import (
    COLS,
    CTRL_C,
    CTRL_D,
    CTRL_ENTER,
    CTRL_J,
    CTRL_R,
    CTRL_U,
    DOWN,
    ENTER,
    RIGHT,
    ROWS,
    TM,
    Emulated,
    jumps,
    mismatch,
    model_server,
    story,
    vanished,
)
from scenarios.support.harness import read_store

pytestmark = [pytest.mark.cli, pytest.mark.slow]

# A step: what a failure calls it, what it does to the terminal, and
# whether it erases what stood drawn by design — an undo, a regenerate, an
# answer taking the question's menu away — so no row's moving is judged.
Step = tuple[str, Callable[[Emulated], None], bool]


class TestChatMode:
    def test_every_block_echoes_as_it_played(self, played: "Played") -> None:
        term = played(Layout.MANUSCRIPT)
        problems = run(
            term,
            [
                line("We wait on the quay."),
                line("1 I wait by the rail."),  # thinking
                line("2 I look around."),  # a note
                line("4 What time is it?"),  # system info
                line("5 Look up the river."),  # web search
                line("6 Tell me about the town."),  # a long reply
                line("3 Which way now?"),  # a question
                key("the first answer", ENTER, erases=True),
                line("0 Everything at once."),
                line("I take the left path."),  # an answer typed
            ],
        )
        assert not problems, "\n".join(problems)

    def test_answers_walked_and_taken_into_the_line(self, played: "Played") -> None:
        term = played(Layout.MANUSCRIPT)
        problems = run(
            term,
            [
                # judged once sent: the menu walked and an answer taken into
                # the line are the prompt's, which no echo shows
                line("3 Which way?"),
                keys("the second answer", DOWN, ENTER, erases=True),
                line("3 Again?"),
                keys("an answer taken into the line, sent", RIGHT, ENTER, erases=True),
            ],
        )
        assert not problems, "\n".join(problems)

    def test_a_failure_regenerated_and_undone(self, played: "Played") -> None:
        term = played(Layout.MANUSCRIPT)
        problems = run(
            term,
            [
                line("Go on."),
                line("-1 Fail."),
                key("regenerate, failing again", CTRL_R, erases=True),
                key("undo the failed line", CTRL_U, erases=True),
                key("undo the turn before", CTRL_U, erases=True),
                line("Short again."),
            ],
        )
        assert not problems, "\n".join(problems)

    def test_the_verbose_line_stands_beside_its_turn(self, played: "Played") -> None:
        term = played(Layout.MANUSCRIPT, verbose=True)
        problems = run(
            term,
            [
                line("2,4 With stats."),
                line("3 Which one?"),
                key("the first answer", ENTER, erases=True),
            ],
        )
        assert not problems, "\n".join(problems)

    def test_a_reply_cut_or_regenerated_mid_stream(self, played: "Played") -> None:
        term = played(Layout.MANUSCRIPT)
        problems = run(
            term,
            [
                midway("6 Long.", CTRL_C),
                midway("6 Long again.", CTRL_R),
                key("undo", CTRL_U, erases=True),
                line("Short."),
            ],
        )
        assert not problems, "\n".join(problems)

    def test_a_resumed_reply_goes_with_its_tool_rounds(self, played: "Played") -> None:
        """A launch echoes a reply with tool rounds as one unit: a
        regenerate redraws every round, and an undo takes every round off
        the screen, as the backend takes every row."""
        term = played(Layout.MANUSCRIPT, ("The ferry left.", "4 What time is it?"))
        problems = run(term, [key("regenerate", CTRL_R, erases=True)])
        assert not problems, "\n".join(problems)
        term.settle(0.4)
        term.send(CTRL_U, 0.2)
        term.quiet()
        left = [row for row in term.lines() if "What time is it?" in row or row.startswith("│")]
        assert not left, left


class TestStoryMode:
    def test_every_block_flows_as_it_played(self, played: "Played") -> None:
        term = played(Layout.CANVAS)
        problems = run(
            term,
            [
                line("She opened the door."),
                line("1 She waited."),
                line("2 She looked round."),
                line("4 What hour was it?"),
                line("5 She looked it up."),
                line("6 And the town?"),
                line("7 And"),  # a reply going on mid-sentence
                line("8 Later."),  # a reply opening a paragraph
                key("the empty line, a continue", ENTER),
                line("3 Which way?"),
                key("the first answer", ENTER, erases=True),
                line("0 All at once."),
                line("She went left."),  # answering a reply with tool rounds
            ],
            story_mode=True,
        )
        assert not problems, "\n".join(problems)

    def test_an_answered_reply_keeps_every_round(self, played: "Played") -> None:
        """Answering takes the question off the text and draws the reply
        again without it — every round of it, not the last alone."""
        term = played(Layout.CANVAS)
        problems = run(term, [line("0 All at once."), line("She went left.")], story_mode=True)
        assert not problems, "\n".join(problems)
        shown = " ".join("".join(row.ljust(80) for row in term.lines()).split())
        store = read_store(term.root)
        try:
            rows = store.stories.get_messages(store.stories.list()[0].id)
        finally:
            store.close()
        for row in rows:
            prose = " ".join(row.body.split("```")[0].split())
            if row.role == "assistant" and prose:
                assert prose[:40] in shown, prose[:60]

    def test_a_sentence_ending_on_the_last_column_keeps_its_last_letter(
        self, played: "Played"
    ) -> None:
        """A call's note repaints the status row the moment the sentence
        before it fills its row to the last column, where the cursor waits
        to wrap: the repaint waits for the text to move on, so the words
        after the call never land on that last letter — some terminals
        forget the wait across the repaint's cursor restore."""
        term = played(Layout.CANVAS)
        said = "Go on."
        # the reply begins after the reader's line and the join's space,
        # the terminal wrapping letter by letter
        start = (term.cursor[1] + len(said) + 1) % term.cols
        sentence = _filling(term.cols - start if term.cols - start >= 20 else 2 * term.cols - start)
        call = "```otk-system-info\n```"

        def script(body: dict[str, Any]) -> str | tuple[str, str]:
            messages = body.get("messages") or []
            if len(messages) >= 2 and sentence in TM.content_text(messages[-2]):
                # the round after the call, a paragraph after it: the prompt
                # draws the text's last line again, which would mend that row
                return "The rest of it came slowly.\n\nThen the bells stopped."
            if messages and said in TM.content_text(messages[-1]):
                return f"{sentence}\n\n{call}"
            return TM.reply(body)  # the lore pass, and anything else

        played.server.script = script
        problems = run(term, [line(said)], story_mode=True)
        assert not problems, "\n".join(problems)
        # the premise: the sentence did fill its row to the last column
        assert any(len(row) == term.cols and row.endswith(sentence[-8:]) for row in term.lines())

    def test_a_line_opening_with_a_dash_runs_on(self, played: "Played") -> None:
        term = played(Layout.CANVAS)
        problems = run(term, [line("- Try."), line("-2 Again.")], story_mode=True)
        assert not problems, "\n".join(problems)

    def test_a_failed_regenerate_keeps_the_text_open(self, played: "Played") -> None:
        term = played(Layout.CANVAS)
        problems = run(
            term,
            [
                line("Go on."),
                line("-1 Maybe fail."),
                key("regenerate, failing again", CTRL_R, erases=True),
                line("And on."),
                key("undo", CTRL_U, erases=True),
                key("regenerate, failing again", CTRL_R, erases=True),
            ],
            story_mode=True,
        )
        assert not problems, "\n".join(problems)

    def test_a_question_after_a_reply_taller_than_the_screen_goes(self, played: "Played") -> None:
        """The question alone is taken off — where its block began, always
        in view — when the reply asking it ran up past the screen's top."""
        term = played(Layout.CANVAS, rows=14)
        problems = run(
            term, [line("36 Which way, long?"), line("I take the hill.")], story_mode=True
        )
        assert not problems, "\n".join(problems)
        assert not [row for row in term.lines() if row.startswith("│")]

    def test_a_line_of_one_row_keeps_the_paragraph_after_it(self, played: "Played") -> None:
        """On a screen wide enough for the text's last line to be one row,
        going back into the text climbs no row: the blank line a typed
        paragraph opened stays between the line and the reply."""
        term = played(Layout.CANVAS, cols=200)
        problems = run(
            term, [keys("8, a paragraph, sent", b"8", CTRL_ENTER, ENTER)], story_mode=True
        )
        assert not problems, "\n".join(problems)

    def test_the_text_ends_on_the_row_above_the_status_row(self, played: "Played") -> None:
        """A reply ending in the text leaves its row open: at the screen's
        foot nothing scrolls the text up under the prompt standing in it."""
        term = played(Layout.CANVAS, rows=12)
        for n in range(4):
            term.send(f"6 Longer {n}.", 0.3)
            term.send(ENTER, 0.2)
            term.quiet()
            if term.lines()[0].strip():  # the text fills the screen
                assert term.cursor[0] == term.rows - 2, f"turn {n}: {term.lines()}"

    def test_a_resumed_question_answered_and_undone(self, played: "Played") -> None:
        """The launch echo cuts a reply of five rows to the part it shows:
        that part is what an answer draws again and an undo takes back to
        the question."""
        term = played(Layout.CANVAS, ("The ferry left.", "0 All at once."))
        problems = run(
            term, [line("She went left."), key("undo", CTRL_U, erases=True)], story_mode=True
        )
        assert not problems, "\n".join(problems)

    def test_a_reply_cut_or_regenerated_mid_stream(self, played: "Played") -> None:
        term = played(Layout.CANVAS)
        problems = run(
            term,
            [
                midway("6 Long.", CTRL_C),
                midway("6 Long again.", CTRL_R),
                key("undo", CTRL_U, erases=True),
                line("Short."),
            ],
            story_mode=True,
        )
        assert not problems, "\n".join(problems)

    def test_the_text_never_blinks(self, played: "Played") -> None:
        """The prompt stands in the text: neither sending a line nor the
        reply landing shows the paragraph gone for a frame, nor the cursor
        dropping under the text on its way back into it."""
        term = played(Layout.CANVAS)
        rows = term.lines()
        opening = rows[next(i for i, row in enumerate(rows) if "Resumed at message" in row) + 2]
        # no digits: the test model reads a line's number as the blocks to send
        for n in ("one", "two", "three", "four"):
            term.send(f"Line {n}.", 0.4)
            with term.recording() as frames:
                term.send(ENTER, 0.1)
                term.quiet()
            end_rows, cursor_row, _, _ = frames[-1]
            end = end_rows[cursor_row][: term.cursor[1]].strip()[-25:]
            landed = next(i for i, (rows, *_) in enumerate(frames) if any(end in r for r in rows))
            assert not vanished(frames, opening[:30]), f"line {n}: the paragraph went"
            assert not vanished(frames, end, after=landed), f"line {n}: the text's end went"
            below = [
                where
                for *_, where in frames[landed:]
                if where is not None and where[0] > cursor_row
            ]
            assert not below, f"line {n}: the cursor showed under the text at {below[:3]}"


class TestStoryModeKeys:
    def test_enter_sends_and_ctrl_enter_opens_a_paragraph(self, played: "Played") -> None:
        term = played(Layout.CANVAS)
        term.send("One.", 0.4)
        term.send(ENTER, 0.2)
        term.quiet()
        term.send("Two.", 0.3)
        term.send(CTRL_J, 0.3)  # a paragraph on every terminal
        term.send("Three.", 0.3)
        term.send(CTRL_ENTER, 0.3)  # as a terminal honouring modifyOtherKeys reports it
        term.send("Four.", 0.3)
        term.send(ENTER, 0.2)
        term.quiet()
        sent = [
            m["content"]
            for r in played.server.requests
            for m in r.get("messages", [])
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        assert any(text.endswith("One.") for text in sent)
        assert any(text.endswith("Two.\n\nThree.\n\nFour.") for text in sent)

    def test_the_cursor_colour_is_handed_back(self, played: "Played") -> None:
        """The story-mode caret paints the terminal's cursor; typing, the
        prompt's end and quitting hand the terminal its colour back, and
        chat mode never paints it."""
        term = played(Layout.CANVAS)
        assert painted(term.raw)
        term.send("H", 0.5)
        assert not painted(term.raw)
        term.send(b"\x7f", 0.5)
        assert painted(term.raw)
        term.send(CTRL_D, 0.5)
        term.settle(1.5)
        assert not painted(term.raw)
        chat = played(Layout.MANUSCRIPT)
        assert b"\x1b]12;" not in chat.raw


# ---------- helpers ----------


class Played:
    """Launches the app in a pty over a story seeded through the backend,
    one state dir each; ends every terminal and the server with the test."""

    def __init__(self, tmp_path: Path) -> None:
        self.server = model_server()
        self._tmp_path = tmp_path
        self._terms: list[Emulated] = []

    def __call__(
        self,
        layout: Layout,
        lines: tuple[str, ...] = ("The ferry left at dawn.",),
        *,
        verbose: bool = False,
        rows: int = ROWS,
        cols: int = COLS,
    ) -> Emulated:
        root = self._tmp_path / f"state{len(self._terms)}"
        story(root, self.server, layout, lines, verbose=verbose)
        term = Emulated(root, rows=rows, cols=cols)
        term.quiet(still=1.5)
        self._terms.append(term)
        return term

    def close(self) -> None:
        for term in self._terms:
            with contextlib.suppress(OSError):  # already gone: it quit on Ctrl+D
                term.quit()
        self.server.close()


@pytest.fixture
def played(tmp_path: Path) -> Iterator[Played]:
    launcher = Played(tmp_path)
    yield launcher
    launcher.close()


def line(text: str) -> Step:
    """A line typed and sent."""

    def act(term: Emulated) -> None:
        term.send(text, 0.3)
        term.send(ENTER, 0.2)

    return text, act, False


def keys(name: str, *presses: bytes, erases: bool = False) -> Step:
    """Keys pressed one after another, typing included."""

    def act(term: Emulated) -> None:
        for data in presses:
            term.send(data, 0.3)

    return name, act, erases


def key(name: str, data: bytes, *, erases: bool = False) -> Step:
    """A key, pressed once the prompt has settled."""

    def act(term: Emulated) -> None:
        term.settle(0.4)
        term.send(data, 0.2)

    return name, act, erases


def _filling(length: int) -> str:
    """A sentence exactly `length` letters long, its full stop the last."""
    words = ("The bells rang over the water once more " * 4)[: length - 1].rstrip()
    return words.ljust(length - 1, "s") + "."


def midway(text: str, data: bytes) -> Step:
    """A line sent, and `data` pressed while its reply streams."""

    def act(term: Emulated) -> None:
        term.send(text, 0.3)
        term.send(ENTER, 0.5)
        term.send(data, 0.2)

    return f"{text} then {data!r} mid-stream", act, False


def run(term: Emulated, steps: list[Step], *, story_mode: bool = False) -> list[str]:
    """Each step played and judged: the rows that moved while it drew —
    not for a step that erases by design (`Step`) — and the screen against
    a fresh launch's echo."""
    problems = []
    for name, act, erasing in steps:
        with term.recording() as frames:
            act(term)
            term.quiet()
        differs = mismatch(term, story_mode=story_mode)
        if differs:
            # Both captures wait for a moment's silence, not for the turn's
            # end: a second look after a longer one tells a slow end of
            # turn from a screen that is wrong.
            term.quiet(still=3.0)
            later = mismatch(term, story_mode=story_mode, still=4.0)
            verdict = "still there after a longer quiet" if later else "gone after a longer quiet"
            differs = [f"{problem} [{verdict}]" for problem in differs]
        found = ([] if erasing else jumps(frames)) + differs
        problems += [f"{name}: {problem}" for problem in found[:6]]
    return problems


def painted(raw: bytes) -> bool:
    """Whether the cursor stands painted: the last colour escape written
    paints it rather than handing the terminal's colour back."""
    return raw.rfind(b"\x1b]12;") > raw.rfind(b"\x1b]112\x07")
