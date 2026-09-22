"""Tagged blocks in a reply: `<otk-NAME>…</otk-NAME>` spans that are not
story.

The `otk-` namespace is the whole test of what a block is. No list of
tools is consulted (`backend.tools` owns those): a body keeps its tags
for good while the tools come and go between builds, so "not story" has
to be a property of the TEXT — a block stays one in a build that never
heard of its tool. A tag matches in any case, ASCII letters only; a
block never closed runs to the end of the text (a reply cut mid-block);
any markup outside the namespace is prose. Pure strings.
"""

import re
import string
from dataclasses import dataclass

NAMESPACE = "otk-"
# A name is letters alone, and no longer than this — which is also how
# long a tail that may still become an opening tag is held back.
_MAX_NAME = 32
_FLAGS = re.IGNORECASE | re.ASCII
_OPENING = re.compile(rf"<{NAMESPACE}([a-z]{{1,{_MAX_NAME}}})>", _FLAGS)
_NAME_SO_FAR = re.compile(rf"[a-z]{{0,{_MAX_NAME}}}")
# ASCII letters folded and nothing else, as the patterns match: a
# lookalike (the long s, the Kelvin sign) must not spell a tag.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


@dataclass(frozen=True)
class Prose:
    """Story text, as written."""

    text: str


@dataclass(frozen=True)
class Block:
    """One block's inside, as written. From a `Splitter` it is a PIECE of
    one, and `closed` marks the piece the closing tag ended."""

    tag: str  # the NAME in <otk-NAME>, lowercase whatever case the text used
    text: str
    closed: bool  # False: the text ended before the closing tag


class Splitter:
    """`split` over a stream: `feed` returns what a delta decides, `flush`
    what was still held when the stream ended. A tail that may yet open
    or close a tag is held back until it does or cannot. Whatever the
    chunking, the pieces joined — prose together, a block's up to its
    closed piece — are `split`'s segments."""

    def __init__(self) -> None:
        self._held = ""
        self._current_tag: str | None = None

    @property
    def current_tag(self) -> str | None:
        """The tag of the block the stream is in; None in prose. Still
        set after `flush` when the stream ended before the closing tag."""
        return self._current_tag

    def feed(self, delta: str) -> list[Prose | Block]:
        text = self._held + delta
        out: list[Prose | Block] = []
        while True:
            if self._current_tag is None:
                found = _OPENING.search(text)
                if found is None:
                    break
                if found.start():
                    out.append(Prose(text[: found.start()]))
                self._current_tag = found.group(1).lower()
            else:
                # Only its own closing tag ends a block.
                closing = re.escape(self._closing(self._current_tag))
                found = re.search(closing, text, _FLAGS)
                if found is None:
                    break
                # Even when empty: the piece is what says the block ended.
                out.append(Block(self._current_tag, text[: found.start()], closed=True))
                self._current_tag = None
            text = text[found.end() :]
        # No whole tag is left in `text`; only its tail may become one.
        if self._current_tag is None:
            held = self._maybe_opening(text)
        else:
            held = self._maybe_closing(text, self._current_tag)
        decided = len(text) - held
        self._held = text[decided:]
        if decided:
            out.append(self._piece(text[:decided]))
        return out

    def flush(self) -> list[Prose | Block]:
        held, self._held = self._held, ""
        return [self._piece(held)] if held else []

    def _piece(self, text: str) -> Prose | Block:
        if self._current_tag is None:
            return Prose(text)
        return Block(self._current_tag, text, closed=False)

    @staticmethod
    def _closing(tag: str) -> str:
        return f"</{NAMESPACE}{tag}>"

    @staticmethod
    def _maybe_opening(text: str) -> int:
        """How much of the end of `text` could still grow into an opening
        tag: from its last `<`, when what follows is `otk-NAME` as far as
        it has come."""
        at = text.rfind("<")
        if at < 0:
            return 0
        tail = text[at:].translate(_ASCII_LOWER)
        start = "<" + NAMESPACE
        if len(tail) <= len(start):
            return len(tail) if start.startswith(tail) else 0
        grows = tail.startswith(start) and _NAME_SO_FAR.fullmatch(tail[len(start) :])
        return len(tail) if grows else 0

    @staticmethod
    def _maybe_closing(text: str, tag: str) -> int:
        """How much of the end of `text` could still grow into `tag`'s
        closing tag: the longest tail that is a proper prefix of it."""
        closing = Splitter._closing(tag)
        for size in range(min(len(closing) - 1, len(text)), 0, -1):
            if closing.startswith(text[-size:].translate(_ASCII_LOWER)):
                return size
        return 0


def split(body: str) -> list[Prose | Block]:
    """The body as segments in order. A block's `closed` is False when
    the body ended first. Empty prose is never a segment; an empty block
    is one."""
    splitter = Splitter()
    out: list[Prose | Block] = []
    for piece in [*splitter.feed(body), *splitter.flush()]:
        last = out[-1] if out else None
        if isinstance(piece, Prose) and isinstance(last, Prose):
            out[-1] = Prose(last.text + piece.text)
        elif isinstance(piece, Block) and isinstance(last, Block) and not last.closed:
            out[-1] = Block(piece.tag, last.text + piece.text, closed=piece.closed)
        else:
            out.append(piece)
    return out


def strip(body: str) -> str:
    """The body without its blocks, tags and all — the story alone, as
    the lore pass reads it; nothing else changes."""
    return "".join(part.text for part in split(body) if isinstance(part, Prose))
