"""Tool calls: the `<otk-NAME>…</otk-NAME>` spans in a reply, which are
the model using a tool and are not story.

The `otk-` namespace is the whole test of what a tool call is. No list
of tools is consulted (`backend.tools` owns those): a body keeps its
tags for good while the tools come and go between builds, so "not
story" has to be a property of the TEXT — a call stays one in a build
that never heard of its tool. A tag matches in any case, ASCII letters
only; a call never closed runs to the end of the text (a reply cut
mid-call); any markup outside the namespace is prose. Calls never nest:
an opening tag inside a call ends that call and opens its own, a
closing tag ends whatever call is open whatever name it carries, and a
closing tag with no call open is noise of the namespace and is dropped.

Two directions, one grammar: a reply is read into prose and calls as
it streams (`ReplyParser`) or whole (`parse_reply`), and a stored reply
goes back on the wire under the story's `ToolSet` (`to_wire`) or into
the lore pass without its calls (`strip`). Pure strings.
"""

import re
import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

NAMESPACE = "otk-"
# A name is letters alone, and no longer than this — which is also how
# long a tail that may still become an opening tag is held back.
_MAX_NAME = 32
_FLAGS = re.IGNORECASE | re.ASCII
# Either tag, whichever comes first: what ends a call, or what opens one.
_TAG = re.compile(rf"<(/?){NAMESPACE}([a-z]{{1,{_MAX_NAME}}})>", _FLAGS)
_NAME_SO_FAR = re.compile(rf"[a-z]{{0,{_MAX_NAME}}}")
# ASCII letters folded and nothing else, as the patterns match: a
# lookalike (the long s, the Kelvin sign) must not spell a tag.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


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
    marks the piece the closing tag ended."""

    name: str  # the NAME in <otk-NAME>, lowercase whatever case the text used
    text: str
    closed: bool  # False: the text ended before the closing tag


class ReplyParser:
    """A reply read into prose and tool calls as it streams: `feed`
    returns what a delta decides, `flush` what was still held when the
    stream ended. A tail that may yet open or close a tag is held back
    until it does or cannot. Whatever the chunking, the pieces joined —
    prose together, a call's up to its closed piece — are
    `parse_reply`'s segments."""

    def __init__(self) -> None:
        self._held = ""
        self._current_call: str | None = None

    @property
    def current_call(self) -> str | None:
        """The name of the call the stream is inside; None in prose.
        Still set after `flush` when the stream ended before the closing
        tag."""
        return self._current_call

    def feed(self, delta: str) -> list[Prose | ToolCall]:
        text = self._held + delta
        out: list[Prose | ToolCall] = []
        while True:
            found = _TAG.search(text)
            if found is None:
                break
            is_closing, name = found.group(1) == "/", found.group(2).lower()
            before = text[: found.start()]
            if self._current_call is None:
                # In prose: an opening tag opens; a closing tag with nothing
                # open is noise of the namespace and is dropped.
                if before:
                    out.append(Prose(before))
                if not is_closing:
                    self._current_call = name
            else:
                # In a call: either tag ends it — even when empty, the piece
                # is what says the call ended — and an opening tag opens its
                # own, since calls never nest.
                out.append(ToolCall(self._current_call, before, closed=True))
                self._current_call = None if is_closing else name
            text = text[found.end() :]
        # No whole tag is left in `text`; only its tail may become one.
        held = self._maybe_tag(text)
        decided = len(text) - held
        self._held = text[decided:]
        if decided:
            out.append(self._piece(text[:decided]))
        return out

    def flush(self) -> list[Prose | ToolCall]:
        held, self._held = self._held, ""
        return [self._piece(held)] if held else []

    def _piece(self, text: str) -> Prose | ToolCall:
        if self._current_call is None:
            return Prose(text)
        return ToolCall(self._current_call, text, closed=False)

    @staticmethod
    def _maybe_tag(text: str) -> int:
        """How much of the end of `text` could still grow into a tag of the
        namespace, opening or closing: from its last `<`, when what follows
        is `/`, `otk-` and a name as far as it has come."""
        at = text.rfind("<")
        if at < 0:
            return 0
        tail = text[at:].translate(_ASCII_LOWER)
        for start in ("<" + NAMESPACE, "</" + NAMESPACE):
            if len(tail) <= len(start):
                if start.startswith(tail):
                    return len(tail)
            elif tail.startswith(start) and _NAME_SO_FAR.fullmatch(tail[len(start) :]):
                return len(tail)
        return 0


def closing(name: str) -> str:
    """The tag that closes a call of `name` — what a request stops on,
    and what is put back where a server kept it."""
    return f"</{NAMESPACE}{name}>"


def parse_reply(body: str) -> list[Prose | ToolCall]:
    """The body as segments in order. A call's `closed` is False when the
    body ended first. Empty prose is never a segment; an empty call is
    one."""
    parser = ReplyParser()
    out: list[Prose | ToolCall] = []
    for piece in [*parser.feed(body), *parser.flush()]:
        last = out[-1] if out else None
        if isinstance(piece, Prose) and isinstance(last, Prose):
            out[-1] = Prose(last.text + piece.text)
        elif isinstance(piece, ToolCall) and isinstance(last, ToolCall) and not last.closed:
            out[-1] = ToolCall(piece.name, last.text + piece.text, closed=piece.closed)
        else:
            out.append(piece)
    return out


def to_wire(body: str, tools: ToolSet) -> str:
    """The body as it goes on the wire under `tools`: a call of a tool
    that is on as its canonical self — the inside byte for byte between
    a lowercase opening tag and its closing tag, whatever the text
    spelled or left open, so the history teaches closed calls; one of a
    tool that is off replaced by its rule's text; any other removed. A
    call that leaves takes the blank line beside it — after it, or
    before it at the end — so no seam is left."""
    out: list[str] = []
    at = 0
    while True:
        found = _TAG.search(body, at)
        if found is None:
            out.append(body[at:])
            break
        if found.group(1) == "/":
            # A closing tag with nothing open: noise, dropped from the wire.
            out.append(body[at : found.start()])
            at = found.end()
            continue
        opened, name = found, found.group(2).lower()
        # The call ends at the next tag of either kind — a closing tag
        # goes with it, an opening tag stays to open its own — or at the end.
        ended = _TAG.search(body, opened.end())
        if ended is None:
            end = stop = len(body)
        elif ended.group(1) == "/":
            end, stop = ended.end(), ended.start()
        else:
            end = stop = ended.start()
        inside = body[opened.end() : stop]
        if name in tools.on:
            out.append(body[at : opened.start()] + f"<{NAMESPACE}{name}>{inside}{closing(name)}")
            at = end
            continue
        rule = tools.off.get(name)
        made = rule(inside) if rule else ""
        before = body[at : opened.start()]
        if made:
            out.append(before + made)
            at = end
        else:
            # Gone: the whitespace run after it goes too, or the one before
            # it where the body ends there.
            trailing = len(body) - len(body[end:].lstrip()) - end
            if end + trailing >= len(body):
                before = before.rstrip()
            out.append(before)
            at = end + trailing
    return "".join(out)


def strip(body: str) -> str:
    """The body without its tool calls, tags and all — the story alone,
    as the lore pass reads it; nothing else changes."""
    return "".join(part.text for part in parse_reply(body) if isinstance(part, Prose))
