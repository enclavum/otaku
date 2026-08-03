"""How many terminal rows a stream of output occupies.

`RowTracker` simulates the cursor of a VT100-family terminal consuming the
exact text otaku prints, so a caller that wants to take output back knows
how many rows to erase. `rows` counts completed row advances — the rows
ABOVE the one the cursor is on — because an erase moves up that many rows
and clears downward, taking the current partial row with it.

The simulation follows what a real terminal behind a cooked tty does:

- A printable character takes its display width in columns (`get_cwidth`:
  wide CJK glyphs two, combining marks zero). Filling the last column does
  NOT advance: the terminal defers the wrap until the next printable
  character arrives, so a line of exactly the width followed by a newline
  occupies one row, not two. A wide character that no longer fits wraps
  before it prints.
- ``\\n`` is one advance to column 0 (ONLCR: the tty turns LF into CR LF).
- ``\\r`` rewinds to column 0; ``\\t`` advances to the next 8-column stop
  but never past the last column and never wraps; ``\\b`` steps one column
  back, never past 0. All three clear a pending wrap; other control
  characters are ignored.
- Escape sequences occupy nothing: CSI (``ESC [`` through its final byte),
  OSC (``ESC ]`` through BEL or ``ESC \\``), and every other two-byte
  ``ESC x`` form. The parse state survives `feed` boundaries, so a
  sequence split across streamed chunks still counts as nothing.

The width is fixed at construction (clamped to at least 1): the tracker
describes what was printed at that width, and a caller who finds the
terminal resized must throw the count away rather than trust it.
"""

from prompt_toolkit.utils import get_cwidth

_TAB_STOP = 8

# Escape-parse states: plain text, ESC seen, inside CSI, inside OSC, and
# ESC seen inside OSC (a string terminator, ESC \, may be forming).
_TEXT, _ESC, _CSI, _OSC, _OSC_ESC = range(5)


class RowTracker:
    """Feed printed text; read `rows` (completed advances since creation
    or `reset`) and `column` (0-based)."""

    def __init__(self, width: int) -> None:
        self.width = max(1, width)
        self.rows = 0
        self.column = 0
        self._pending = False  # last column filled, the wrap deferred
        self._state = _TEXT

    def feed(self, text: str) -> None:
        for ch in text:
            self._consume(ch)

    def reset(self) -> None:
        self.rows = 0
        self.column = 0
        self._pending = False
        self._state = _TEXT

    def _consume(self, ch: str) -> None:
        if self._state != _TEXT:
            self._escape(ch)
            return
        if ch == "\x1b":
            self._state = _ESC
            return
        if ch == "\n":
            self.rows += 1
            self.column = 0
            self._pending = False
            return
        if ch == "\r":
            self.column = 0
            self._pending = False
            return
        if ch == "\t":
            stop = self.column // _TAB_STOP * _TAB_STOP + _TAB_STOP
            self.column = min(stop, self.width - 1)
            self._pending = False
            return
        if ch == "\b":
            self.column = max(0, self.column - 1)
            self._pending = False
            return
        width = get_cwidth(ch)
        if width <= 0:
            return  # combining marks and stray controls occupy nothing
        if self._pending or self.column + width > self.width:
            self.rows += 1
            self.column = 0
            self._pending = False
        self.column += width
        if self.column >= self.width:
            self.column = self.width
            self._pending = True

    def _escape(self, ch: str) -> None:
        if self._state == _ESC:
            if ch == "[":
                self._state = _CSI
            elif ch == "]":
                self._state = _OSC
            else:
                self._state = _TEXT  # a two-byte form: ESC plus this char
        elif self._state == _CSI:
            if "\x40" <= ch <= "\x7e":
                self._state = _TEXT
        elif self._state == _OSC:
            if ch == "\x07":
                self._state = _TEXT
            elif ch == "\x1b":
                self._state = _OSC_ESC
        elif self._state == _OSC_ESC:
            self._state = _TEXT if ch == "\\" else _OSC


def measure(text: str, width: int) -> int:
    """Rows `text` occupies when printed at `width` — the advances a fresh
    tracker sees. A printed line or block is measured with its trailing
    newline: ``measure("hello\\n", 80) == 1``."""
    tracker = RowTracker(width)
    tracker.feed(text)
    return tracker.rows
