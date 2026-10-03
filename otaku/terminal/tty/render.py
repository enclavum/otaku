"""How a turn looks — decided once, used by the prompt's live coloring,
the played block, the resume echo, and the story browser alike. A
request has its commands picked out, and the pictures it carried noted
after them in dim brackets; a reply is typeset the way it streamed —
one or the other, never both: they are counterparts, not layers. The
reply goes through the very typesetter it arrived on, run to the end
in one pass, so a turn echoed on resume is the same text that was on
screen when it played. The vocabulary comes from `backend.commands`, so
what counts as a command is never guessed from a slash.

A reply's tool calls are drawn as what they are, never as their tags,
each a BLOCK of its own in the flow — a blank line before it, one after
it where prose goes on (`spaced`), a bar opening every row, the text
wrapped at the width and plain (`BlockStream`), streamed piece by piece
and echoed through the same object — a question or a note only while
the story shows its tool (`shown_tools`, the caller's to read off the
story's settings and pass), otherwise left out, fences and all, as the
wire leaves it out. A question is the question alone (`QuestionStream`)
— its answers are the prompt's menu while the story stands on it
(`asked`, the page's `transcript.questionPosed` rule copied), and are
gone with the menu once answered; a note is dim, bar and all. Every
other call still prints as it streamed, fences and all, until its look
is decided.
"""

import io
import re
from dataclasses import replace
from typing import TextIO

from prompt_toolkit.utils import get_cwidth

from otaku.backend import Message
from otaku.backend.api import play as api_play
from otaku.backend.commands import COMMANDS
from otaku.backend.story import StorySettingAssistantNotes, StorySettingQuestions, StorySettings
from otaku.backend.tools import ToolQuestion, get_tool_label
from otaku.formatting import printable
from otaku.terminal.tty import DIM, user_block
from otaku.terminal.tty.cursor import terminal_width
from otaku.terminal.tty.typography import Streamer, highlight_commands

# A command as a spec's token writes it: a slash and a word, nothing
# else — rules out the `…` the inliner rows ride behind.
_COMMAND_WORD = re.compile(r"^/[a-z]+$")
# Intensity back to normal, colors untouched: a reset inside the played
# block would knock its band out mid-row.
_NORMAL = "\x1b[22m"
# What opens each row of a block — a call's, the thinking's, and the
# answers the prompt offers under a question (`prompt.AnswerMenu`): the
# typesetter's blockquote bar (`typography`).
BAR = "│ "


class BlockStream:
    """A call's inside as the reader is shown it: plain — an aside or a
    question, not story, so none of the story's typesetting — behind a
    bar at the margin, `│ ` opening every row of it: the block is
    wrapped here at `width`, the terminal's own rule (a character's
    columns as the ledger's `RowTracker` counts them), so a long line
    never wraps past the bar as a quote's does. A note is `dim`, bar and
    all, and so is the thinking; a question stands at full weight. Drawn
    where the typesetter
    stands as its pieces arrive, streamed and echoed alike (the echo
    feeds it the whole call as one piece, at the same width, so the two
    cannot differ). `open` sends the blank line before it through the
    typesetter, which ends the line it had open and closes its spans,
    then dims past it — the escape would not survive the typesetter's
    sanitizing, so the control bytes are dropped here as it drops them.
    `feed` takes a piece; a word is placed once it is complete — on the
    row if it fits, else at the start of the next, cut across rows when
    wider than one — the whitespace that led the block dropped, a
    newline kept as a row of its own, runs of spaces made one, and what
    trails held back until a word follows or `close` ends the block. The
    block ends its line no more than prose does. Each call returns what
    it wrote, for the caller's count of what has been drawn."""

    def __init__(self, out: TextIO, width: int, *, dim: bool = False) -> None:
        self._out = out
        self._dim = dim
        self._room = max(1, width - _columns(BAR))  # a row's columns past the bar
        self._begun = False  # a word placed: the leading whitespace is over
        self._word = ""  # the word being built — a piece may end inside one
        self._breaks = 0  # newlines waiting to be drawn, once a word follows
        self._space = False  # a space waiting between words
        self._col = 0  # columns taken on the current row, past the bar

    def open(self, streamer: Streamer, before: str) -> str:
        lead = spaced(before)
        streamer.feed(lead)
        streamer.flush()
        return lead + self.begin()

    def begin(self) -> str:
        """The block's start where no typesetter is in the way: its style
        on, nothing before it."""
        style = DIM if self._dim else ""
        self._out.write(style)
        return style

    def feed(self, piece: str) -> str:
        written = "".join(self._take(ch) for ch in printable(piece))
        self._out.write(written)
        self._out.flush()
        return written

    def close(self) -> str:
        written = self._place_word() + (_NORMAL if self._dim else "")
        self._out.write(written)
        return written

    def _take(self, ch: str) -> str:
        """One character of the block's text: a newline or a space ends
        the word being built and waits its turn; anything else builds."""
        if ch == "\n":
            placed = self._place_word()
            self._breaks += 1
            return placed
        if ch.isspace():
            placed = self._place_word()
            self._space = True
            return placed
        self._word += ch
        return ""

    def _place_word(self) -> str:
        """The word built so far, on the page — after the newlines or the
        space waiting before it; nothing for no word, and the whitespace
        keeps waiting."""
        word, self._word = self._word, ""
        if not word:
            return ""
        out = ""
        if not self._begun:
            self._begun = True
            out += BAR
        elif self._breaks:
            out += ("\n" + BAR) * self._breaks
            self._col = 0
        elif self._space:
            if self._col + 1 + _columns(word) > self._room:
                out += "\n" + BAR
                self._col = 0
            else:
                out += " "
                self._col += 1
        self._breaks, self._space = 0, False
        for ch in word:
            cw = _columns(ch)
            if self._col and self._col + cw > self._room:
                out += "\n" + BAR
                self._col = 0
            out += ch
            self._col += cw
        return out


class QuestionStream(BlockStream):
    """A question call's inside as the reader is shown it: the QUESTION
    alone, as a block — its answers are the prompt's menu while the
    story stands on it, not text in the flow, so what stays in the
    scrollback is what a past question shows. The question is the lines
    before the first option, as the tool reads it (`ToolQuestion`), and
    that is decided line by line as the call streams: a line whose first
    token is an option's head (`1.`, `a)`) is held until its end tells
    (`ToolQuestion.is_option`), and once a line is an option, it and
    everything after it is dropped; every other line streams word by
    word."""

    def __init__(self, out: TextIO, width: int) -> None:
        super().__init__(out, width)
        self._line = ""  # the line so far, while it may still turn out an option
        self._decided = False  # this line is the question's: its characters pass as they come
        self._options = False  # the options have begun: nothing after is the question

    def feed(self, piece: str) -> str:
        written = "".join(self._filter(ch) for ch in printable(piece))
        self._out.write(written)
        self._out.flush()
        return written

    def close(self) -> str:
        held = ""
        if not self._options and not self._decided and not ToolQuestion.is_option(self._line):
            held = self._pass(self._line)  # a last line without its newline, the question's
        self._line = ""
        self._out.write(held)
        return held + super().close()

    def _filter(self, ch: str) -> str:
        if self._options:
            return ""
        if ch == "\n":
            out = ""
            if not self._decided:
                if ToolQuestion.is_option(self._line):
                    self._options = True
                    return ""
                out = self._pass(self._line)
            self._line, self._decided = "", False
            return out + self._pass("\n")
        if self._decided:
            return self._pass(ch)
        self._line += ch
        # At the first token's end, the line tells: an option's head keeps
        # it held to its newline; anything else is the question's, and flows.
        if (
            ch.isspace()
            and self._line.strip()
            and not ToolQuestion.is_option(self._line.strip() + " x")
        ):
            self._decided = True
            return self._pass(self._line)
        return ""

    def _pass(self, text: str) -> str:
        return "".join(self._take(ch) for ch in text)


def message(
    text: str,
    role: str,
    *,
    pictures: int = 0,
    shown: frozenset[str] = frozenset(),
    width: int | None = None,
    label: str = "",
) -> str:
    """One body styled for display: highlighted (user) or typeset
    (assistant) — and, for a turn that carried pictures, the dim
    bracketed note on the same line after the words, the stats line's
    family. A reply's questions are drawn as the question alone
    (`QuestionStream`) and its notes dim (`BlockStream`), each while
    `shown` holds its tool (`shown_tools`), else left out — blocks behind the bar,
    wrapped at `width`, the terminal's unless the caller draws into a
    narrower place. Takes no settings besides that — the
    user's colors reached the theme at launch, so a caller only has to
    say WHAT it is drawing.
    The one renderer for showing AND measuring, so the ledger can never
    disagree with an echo."""
    if role == "user":
        styled = highlight_commands(text, command_tokens())
        if pictures:
            marker = f"{DIM}{pictures_note(pictures)}{_NORMAL}"
            styled = f"{styled} {marker}" if styled else marker
        return styled
    out = io.StringIO()
    streamer = Streamer(out)
    if role == "tool":
        # A tool turn — a tool's result: a dim block behind the bar, as
        # a note is drawn, but the tool's `label` ALONE — the result is
        # the model's to read, not the story's, and a terminal has no
        # hover to keep it in. Streamed the same way (`chat.stream`), so
        # the two cannot differ.
        result = BlockStream(out, width or terminal_width(), dim=True)
        result.open(streamer, "")
        result.feed(label or "Tool")
        result.close()
        streamer.flush()
        return out.getvalue()
    fed = ""
    after_call = False  # a call's block was drawn last: prose going on is set apart
    for segment in api_play.segments(text):
        piece = str(segment["text"])
        if segment["kind"] == "prose":
            if after_call:
                if not piece.strip():
                    continue  # the model's own newlines after a block: the blank line is drawn once
                piece = spaced(fed) + piece.lstrip("\n")
                after_call = False
            streamer.feed(piece)
        elif segment["tool"] == "question":
            if "question" not in shown:
                after_call = True  # left out — and the hole it would leave with it
                continue
            block: BlockStream = QuestionStream(out, width or terminal_width())
            piece = block.open(streamer, fed) + block.feed(piece) + block.close()
            after_call = True
        elif segment["tool"] == "note":
            if "note" not in shown:
                after_call = True  # left out — and the hole it would leave with it
                continue
            block = BlockStream(out, width or terminal_width(), dim=True)
            piece = block.open(streamer, fed) + block.feed(piece) + block.close()
            after_call = True
        elif segment.get("actor") == "system":
            # a call otaku runs — the search's query — is the model's,
            # not the story's: nothing drawn, and no hole where it stood;
            # its result follows as a turn of its own
            after_call = True
            continue
        else:
            # the calls with no look yet print as they streamed, in the
            # language's canonical form (`context.tool_calls`), which the
            # terminal may not import — `chat.stream` spells it the same
            piece = f"```otk-{segment['tool']}\n{piece}\n```"
            streamer.feed(piece)
        fed += piece
    streamer.flush()
    return out.getvalue()


def block(text: str, width: int) -> str:
    """`text` whole as a block behind the bar, as `BlockStream` draws it
    at `width` — for a caller that holds the text and draws the rows
    itself: the prompt's answers under a question."""
    out = io.StringIO()
    stream = BlockStream(out, width)
    stream.begin()
    stream.feed(text)
    stream.close()
    return out.getvalue()


def spaced(before: str) -> str:
    """The newlines that set a call's block apart from what was drawn
    before it: what is missing for one blank line, however many the
    model left — none for a block that opens the reply. What FOLLOWS a
    block gets the blank line drawn once, its own leading newlines
    dropped: they were drawn already, so they can be counted."""
    if not before.strip():
        return ""
    trailing = len(before) - len(before.rstrip("\n"))
    return "\n" * max(0, 2 - trailing)


def _columns(text: str) -> int:
    """The columns `text` fills, as the ledger counts them (`cursor`)."""
    return sum(max(0, get_cwidth(ch)) for ch in text)


def shown_tools(settings: StorySettings) -> frozenset[str]:
    """The tools whose calls the reader is shown: a question while its
    tool is on — off, a tool does not exist for the model, and the
    reader sees what the model reads — and the notes while theirs is on
    with its display switch. The page's `transcript.displayTools`, the
    rule copied."""
    shown: set[str] = set()
    questions = settings.get(StorySettingQuestions.name)
    if questions is not None and questions.enabled:
        shown.add("question")
    notes = settings.get(StorySettingAssistantNotes.name)
    if notes is not None and notes.enabled and notes.display_notes:
        shown.add("note")
    return frozenset(shown)


def asked(newest: Message | None, shown: frozenset[str]) -> ToolQuestion | None:
    """The question the story stands on, read: the questions tool is on
    (`shown`), and the newest message is a reply whose last call is a
    question, with nothing after it but notes and whitespace — prose, or
    a call of a tool this build does not know, means the model went on
    past it; a line played since means it was answered. The page's
    `transcript.questionPosed`, the rule copied."""
    if newest is None or newest.role != "assistant" or "question" not in shown:
        return None
    for segment in reversed(api_play.segments(newest.body)):
        if segment["kind"] == "prose":
            if str(segment["text"]).strip():
                return None
            continue
        if segment["tool"] == "question":
            return ToolQuestion(str(segment["text"]))
        if segment["tool"] != "note":
            return None
    return None


def pictures_note(count: int) -> str:
    """The note's plain text — `[ 1 picture ]` — for a caller that
    measures a row before styling it; "" for none."""
    if not count:
        return ""
    return f"[ {count} picture{'s' if count != 1 else ''} ]"


def turn(item: Message, *, shown: frozenset[str] = frozenset()) -> str:
    """One turn exactly as the echoes print it: a user turn as the grey
    block, a model turn as it streamed, the calls of the tools `shown` — the
    trailing newline normalized away, the caller joining and terminating
    lines. The breaks at an inline row's edges are its joins in the
    canvas, and a block stands alone: nothing of them is drawn."""
    body = item.body.strip()
    if item.role == "user":
        return user_block(message(body, "user", pictures=len(item.attachments)))
    label = get_tool_label(item.provider, item.body) if item.role == "tool" else ""
    return "\n".join(message(body, item.role, shown=shown, label=label).splitlines())


def last_turns(
    messages: list[Message],
    count: int,
    *,
    shown: frozenset[str] = frozenset(),
    is_canvas: bool = False,
) -> str:
    """The last `count` turns, echoed the way they played: one blank line
    between turns, bodies verbatim, the calls of the tools `shown` —
    or, on the canvas (`is_canvas`), as the one text they are (`flow`)."""
    if is_canvas:
        return flow(messages[-count:], shown=shown)
    out: list[str] = []
    for item in messages[-count:]:
        out.append("")
        out.append(turn(item, shown=shown))
    return "\n".join(out).lstrip("\n")


def flow(items: list[Message], *, shown: frozenset[str] = frozenset()) -> str:
    """The turns as ONE text — the canvas's echo in this medium: a user
    row as prose, no block, typeset as the text is; every row joined to
    the text before it as `api.play.join_type` says — a paragraph after
    one blank line, an inline row after one space; a tool turn a block
    apart, and the row after it a paragraph, since a block ends its
    line. No trailing newline, like `turn`."""
    return flow_offsets(items, shown=shown)[0]


def flow_offsets(
    items: list[Message], *, shown: frozenset[str] = frozenset()
) -> tuple[str, list[int]]:
    """`flow`'s text, and where each turn's own text begins in it — a
    paragraph join's blank counted with the turn BEFORE it — so the
    ledger can place echoed turns for erasing (`Chat.restore_tail`)."""
    out = ""
    starts: list[int] = []
    previous: Message | None = None
    for item in items:
        # the spaces at a row's edges decide nothing: the join draws its own
        body = replace(item, body=item.body.strip(" \t"))
        piece = prose(body.body) if item.role == "user" else turn(body, shown=shown)
        if not out:
            starts.append(0)
            out = piece
        elif api_play.join_type(previous, item) == "paragraph":
            out = out.rstrip("\n") + "\n\n"
            starts.append(len(out))
            out += piece.lstrip("\n")
        else:
            # the space is the turn's, so an erase of it takes the join too
            starts.append(len(out))
            out += " " + piece
        previous = item
    return out, starts


def prose(text: str) -> str:
    """`text` typeset as a reply's prose is — the reader's line on the
    canvas, which reads as the text does: dialogue takes its accent, and
    no command is picked out, since none is played there."""
    out = io.StringIO()
    streamer = Streamer(out)
    streamer.feed(text)
    streamer.flush()
    return out.getvalue()


def command_tokens() -> tuple[str, ...]:
    """Every slash word the app answers to, in table order — the commands
    typed at a line's start and the inliners typed inside one. What
    display reads to tell a command from prose: `/me` is one, `and/or`
    is not, so a highlighter never has to guess from the slash alone."""
    found = (word for spec in COMMANDS for word in spec.token.split())
    return tuple(dict.fromkeys(word for word in found if _COMMAND_WORD.match(word)))
