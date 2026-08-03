"""Where the cursor is, and how printed text moves it.

Two views of the same thing, both serving the screen-erasing paths:
`RowTracker` simulates the cursor consuming the exact text otaku prints,
so a caller that wants to take output back knows how many rows to erase;
`cursor_row` asks the terminal itself (DSR 6 → CPR) where the cursor
really is — the ground truth that already accounts for scroll regions,
prompt_toolkit's menu scrolling, and everything else that moved the
screen. `terminal_width` is the width the simulation wraps and measures
at. Only `cursor_row` touches the terminal; everything else is pure, and
the unit tests cover exactly that pure surface.

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

import contextlib
import os
import re
import select
import shutil
import sys
import time

from prompt_toolkit.utils import get_cwidth

# POSIX-only raw-terminal control, exactly like the in-stream watcher's:
# absent on Windows, where the cursor query degrades to "no answer".
try:
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

_TAB_STOP = 8

# Escape-parse states: plain text, ESC seen, inside CSI, inside OSC, and
# ESC seen inside OSC (a string terminator, ESC \, may be forming).
_TEXT, _ESC, _CSI, _OSC, _OSC_ESC = range(5)

_QUERY = "\x1b[6n"
_RESPONSE = re.compile(rb"\x1b\[\??(\d+);\d+R")
_DEADLINE = 0.2


class RowTracker:
    """Feed printed text; read `rows` (completed advances since creation
    or `reset` — the rows ABOVE the one the cursor is on, because an erase
    moves up that many rows and clears downward, taking the current
    partial row with it) and `column` (0-based)."""

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


def terminal_width() -> int:
    """The width everything inline prints and measures at — floored, so
    degenerate reports (a zero-size pty) still measure sanely."""
    return max(20, shutil.get_terminal_size((80, 24)).columns)


def cursor_row() -> int | None:
    """The cursor's 1-based screen row, or None when it cannot be known:
    no TTY, no termios, or a terminal silent past the deadline — callers
    fall back to not erasing. The terminal must be in cooked mode (between
    prompts, after a stream) — the query takes it raw for the exchange and
    always restores it. Bytes typed while the query is in flight are read
    with the response and dropped: the window is a few milliseconds, and a
    swallowed keystroke costs one re-press, while preserving it would cost
    a screen model."""
    if termios is None or tty is None:
        return None
    try:
        fd = sys.stdin.fileno()
    except (ValueError, OSError):
        return None
    if not os.isatty(fd) or not sys.stdout.isatty():
        return None
    try:
        orig = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except termios.error:
        return None
    try:
        sys.stdout.write(_QUERY)
        sys.stdout.flush()
        return _read_response(fd)
    except OSError:
        return None
    finally:
        with contextlib.suppress(termios.error):
            termios.tcsetattr(fd, termios.TCSANOW, orig)


def _read_response(fd: int) -> int | None:
    data = b""
    deadline = time.monotonic() + _DEADLINE
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        ready, _, _ = select.select([fd], [], [], left)
        if not ready:
            return None
        chunk = os.read(fd, 64)
        if not chunk:
            return None
        data += chunk
        match = _RESPONSE.search(data)
        if match:
            return int(match.group(1))
