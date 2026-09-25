"""Tool calls: the fenced blocks of a reply marked with the `otk-`
namespace, which are the model using a tool and are not story.

A call is a fenced code block whose info string names the tool:

    ```otk-question
    Go in?
    1. Yes
    2. No
    ```

The namespace is the whole test of what a call is. No list of tools is
consulted (`backend.tools` owns those): a body keeps its fences for
good while the tools come and go between builds, so "not story" has to
be a property of the TEXT — a call stays one in a build that never
heard of its tool. Three rules, and one piece of state, the open call:
a fence line whose info string is `otk-` and a name of ASCII letters
(any case, 32 at most, spaces allowed around it) opens a call, ending
the one open, so calls never nest; a bare fence line ends the open
call, and is prose when none is; any other line is what it is where it
stands — prose outside a call, the call's own inside one, a fence with
another info string included, as a markdown viewer reads it. A fence
is a fence only at the start of its line (three backticks or more,
indented three spaces at most). Its line goes with the call, newline
included, and so do the blank lines before it — so a call left out
leaves no hole, and the inside is the lines between with no trailing
blank line. A call never closed runs to the end of the text (a reply
cut mid-call). Plain fences are never tracked: a model that forgot to
close one loses nothing, the next `otk-` fence opens its call all the
same.

Two directions, one grammar: a reply is read into prose and calls as
it streams (`ReplyParser`) or whole (`parse_reply`), and a stored reply
goes back on the wire under the story's `ToolSet` (`to_wire`) or into
the lore pass without its calls (`strip`). Pure strings.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

FENCE = "```"  # bare, the line that closes a call
_NAMESPACE = "otk-"
# A name is letters alone, and no longer than this.
_MAX_NAME = 32
_FLAGS = re.IGNORECASE | re.ASCII
# A whole line (its newline off) that opens a call: the fence, the
# namespace and a name, spaces allowed around the info string.
_OPENER = re.compile(rf" {{0,3}}`{{3,}}[ \t]*{_NAMESPACE}([a-z]{{1,{_MAX_NAME}}})[ \t]*", _FLAGS)
# A whole line that is a bare fence: what closes a call.
_BARE = re.compile(r" {0,3}`{3,}[ \t]*")
# A line still arriving that may yet complete into either — held back
# until its newline decides it, or it cannot.
_SO_FAR = re.compile(
    rf" {{0,3}}`{{0,2}}"
    rf"| {{0,3}}`{{3,}}[ \t]*(?:o|ot|otk|{_NAMESPACE}[a-z]{{0,{_MAX_NAME}}}[ \t]*)?",
    _FLAGS,
)


@dataclass(frozen=True)
class ToolSet:
    """The tools a story knows, by name, each on or off: how their calls
    go on the wire. A call of a tool in `on` is sent as written — the
    history teaches the model the convention; one of a tool in `off`
    becomes what its rule makes of the inside — the story keeps only
    what reads as story; one of any other name leaves the wire — a tool
    this build no longer has. A white list, so a decommissioned tool's
    calls never ride."""

    on: frozenset[str] = frozenset()
    off: Mapping[str, Callable[[str], str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Prose:
    """Story text, as written."""

    text: str


@dataclass(frozen=True)
class ToolCall:
    """The model using a tool, as written: the tool's name and the inside
    of the call. From a `ReplyParser` it is a PIECE of one, and `closed`
    marks the piece the closing fence ended."""

    name: str  # the NAME in `otk-NAME`, lowercase whatever case the text used
    text: str
    closed: bool  # False: the text ended before the closing fence


@dataclass(frozen=True)
class Cut:
    """Where a reply ended at a call of a tool that ends it: `at` is how
    far into the text fed the reply runs — through the closing fence
    where the model wrote one (`fenced`), else to the end of the call's
    last line, the fence being the recorder's to add."""

    at: int
    fenced: bool


class ReplyParser:
    """A reply read into prose and tool calls as it streams: `feed`
    returns what a delta decides, `flush` what was still held when the
    stream ended. A line's start that may yet become a fence is held
    back until its newline decides it, or it cannot, as is a line's end
    until the next line says whose newline it is; the rest of a line
    streams as it comes. Whatever the chunking, the pieces joined —
    prose together, a call's up to its closed piece — are
    `parse_reply`'s segments. A call of a tool in `ending` ENDS THE
    TEXT: `cut` says where, and nothing after it is read."""

    def __init__(self, ending: frozenset[str] = frozenset()) -> None:
        self._ending = ending
        self._held = ""  # the start of a line, not yet decided
        self._call: str | None = None
        self._mid_line = False  # inside a line already decided ordinary
        self._newlines = 0  # line ends held: a fence's if one follows, else the text's
        self._fed = 0  # characters fed so far
        self.cut: Cut | None = None

    def feed(self, delta: str) -> list[Prose | ToolCall]:
        self._fed += len(delta)
        text = self._held + delta
        self._held = ""
        out: list[Prose | ToolCall] = []
        while text and self.cut is None:
            newline = text.find("\n")
            if self._mid_line:
                if newline < 0:
                    self._text(out, text)
                    break
                self._text(out, text[:newline])
                self._newlines += 1
                self._mid_line = False
                text = text[newline + 1 :]
                continue
            line = text if newline < 0 else text[:newline]
            if newline < 0 and _SO_FAR.fullmatch(line):
                self._held = text
                break
            opener = _OPENER.fullmatch(line)
            if opener is not None:
                self._close(out, text, fenced=False)
                self._open(out, opener.group(1))
                text = text[newline + 1 :]
            elif self._call is not None and _BARE.fullmatch(line):
                self._close(out, text, fenced=True)
                text = text[newline + 1 :]
            else:
                self._mid_line = True
        return out

    def flush(self) -> list[Prose | ToolCall]:
        text, self._held = self._held, ""
        out: list[Prose | ToolCall] = []
        if text and self.cut is None:
            # The line is complete now, the text's end ending it.
            opener = _OPENER.fullmatch(text)
            if opener is not None:
                self._close(out, text, fenced=False)
                self._open(out, opener.group(1))
            elif self._call is not None and _BARE.fullmatch(text):
                self._close(out, text, fenced=True)
            else:
                self._text(out, text)
        self._mid_line = False
        return out

    def _open(self, out: list[Prose | ToolCall], name: str) -> None:
        """A call opened: its first piece, empty, says so at once — a
        stream that ends right after the opening fence still yields the
        call — and what follows joins it."""
        if self.cut is not None:
            return
        self._newlines = 0
        self._call = name.lower()
        _push(out, ToolCall(self._call, "", closed=False))

    def _close(self, out: list[Prose | ToolCall], text: str, *, fenced: bool) -> None:
        """The open call, if any, ended by the fence line `text` begins
        with — a bare fence (`fenced`), or the opening fence of the next."""
        if self._call is None:
            return
        line_start = self._fed - len(text)
        if fenced:
            at = line_start + len(text.split("\n", 1)[0])
        else:  # before the newline that ends the last line, held or the opening fence's own
            at = line_start - max(self._newlines, 1)
        if self._call in self._ending:
            self.cut = Cut(at, fenced)
        _push(out, ToolCall(self._call, "", closed=True))
        self._call = None
        self._newlines = 0

    def _text(self, out: list[Prose | ToolCall], text: str) -> None:
        """Ordinary text: prose, or the call's own — after the line ends
        held so far, which the text proves were its own."""
        if not text:
            return
        text = "\n" * self._newlines + text
        self._newlines = 0
        _push(out, Prose(text) if self._call is None else ToolCall(self._call, text, closed=False))


def parse_reply(body: str) -> list[Prose | ToolCall]:
    """The body as segments in order. A call's `closed` is False when the
    body ended first. Empty prose is never a segment; an empty call is
    one."""
    parser = ReplyParser()
    out: list[Prose | ToolCall] = []
    for piece in [*parser.feed(body), *parser.flush()]:
        _push(out, piece)
    return out


def to_wire(body: str, tools: ToolSet) -> str:
    """The body as it goes on the wire under `tools`: a call of a tool
    that is on as its canonical self — the inside byte for byte between
    the canonical opening fence and a bare closing one, whatever the
    text spelled or left open, so the history teaches closed calls; one
    of a tool that is off replaced by its rule's text; any other
    removed. A call that leaves takes the blank line beside it — after
    it, or before it at the end — so no seam is left."""
    out: list[str] = []
    at = 0  # how far the body is written
    for name, start, end, inside in _spans(body):
        before = body[at:start]
        if name in tools.on:
            opening = f"{FENCE}{_NAMESPACE}{name}"
            made = f"{opening}\n{inside}\n{FENCE}" if inside else f"{opening}\n{FENCE}"
        else:
            rule = tools.off.get(name)
            made = rule(inside) if rule else ""
        if made:
            out.append(before + made)
            at = end
            continue
        # Gone: the whitespace run after it goes too, or the one before
        # it where the body ends there.
        trailing = len(body) - len(body[end:].lstrip()) - end
        if end + trailing >= len(body):
            before = before.rstrip()
        out.append(before)
        at = end + trailing
    out.append(body[at:])
    return "".join(out)


def strip(body: str) -> str:
    """The body without its tool calls, fences and all — the story alone,
    as the lore pass reads it: the prose, a newline where a call stood
    between two runs of it (a call is whole lines, so the line before
    and the line after stay two lines); nothing else changes."""
    return "\n".join(part.text for part in parse_reply(body) if isinstance(part, Prose))


def _spans(body: str) -> list[tuple[str, int, int, str]]:
    """Every call of the body: its name, where it starts (its opening
    fence line), where it ends (through its bare closing fence; before
    the newline that precedes the next opening fence; or the body's
    end) and its inside."""
    spans: list[tuple[str, int, int, str]] = []
    call: tuple[str, int, list[str]] | None = None  # name, start, lines

    def close(end: int) -> None:
        if call is not None:
            name, start, lines = call
            spans.append((name, start, end, "\n".join(lines).rstrip("\n")))

    position = 0
    for line in body.split("\n"):
        start, end = position, position + len(line)
        position = end + 1
        opener = _OPENER.fullmatch(line)
        if opener is not None:
            close(start - 1)
            call = (opener.group(1).lower(), start, [])
        elif call is None:
            continue
        elif _BARE.fullmatch(line):
            close(end)
            call = None
        else:
            call[2].append(line)
    close(len(body))
    return spans


def _push(out: list[Prose | ToolCall], piece: Prose | ToolCall) -> None:
    """`piece` onto `out`, joined to the last where the two are one:
    prose runs together, and so does a call's text up to the piece that
    is closed."""
    last = out[-1] if out else None
    if isinstance(piece, Prose):
        if isinstance(last, Prose):
            out[-1] = Prose(last.text + piece.text)
            return
    elif isinstance(last, ToolCall) and not last.closed and last.name == piece.name:
        out[-1] = ToolCall(piece.name, last.text + piece.text, closed=piece.closed)
        return
    out.append(piece)
