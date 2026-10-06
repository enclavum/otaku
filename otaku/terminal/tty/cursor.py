"""How printed text moves the cursor — the pure simulation of a cooked
tty, so a caller that wants to take output back knows how many rows to
erase — and `cursor_row`, the one real ask.

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

import re
import shutil
import sys

from prompt_toolkit.utils import get_cwidth

from otaku.terminal import tty

_CPR = re.compile(rb"\x1b\[\??(\d+);\d+R")

_TAB_STOP = 8

# Escape-parse states: plain text, ESC seen, inside CSI, inside OSC, and
# ESC seen inside OSC (a string terminator, ESC \, may be forming).
_TEXT, _ESC, _CSI, _OSC, _OSC_ESC = range(5)


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

    def rewind(self, rows: int, column: int) -> None:
        """Place the count outright — where an erase left the cursor, or
        where a restored exchange begins: a column at the width is the
        deferred wrap, as `feed` leaves it."""
        self.rows = rows
        self.column = min(column, self.width)
        self._pending = column >= self.width
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
    """The cursor's 1-based screen row, or None when it cannot be known —
    the ground truth the screen-erasing paths check the simulation
    against. On POSIX it is asked of the terminal itself (DSR 6 → CPR,
    through `tty.ask` — cooked mode required, so between prompts, after
    a stream); the Windows consoles answer no escape query but hold the
    same fact one API call away, so there it is read from the screen
    buffer instead."""
    if sys.platform == "win32":
        return _console_row()
    match = tty.ask("\x1b[6n", _CPR)
    return int(match.group(1)) if match else None


if sys.platform == "win32":
    import ctypes

    class _Coord(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class _SmallRect(ctypes.Structure):
        _fields_ = [
            ("Left", ctypes.c_short),
            ("Top", ctypes.c_short),
            ("Right", ctypes.c_short),
            ("Bottom", ctypes.c_short),
        ]

    class _ScreenBufferInfo(ctypes.Structure):
        _fields_ = [
            ("dwSize", _Coord),
            ("dwCursorPosition", _Coord),
            ("wAttributes", ctypes.c_ushort),
            ("srWindow", _SmallRect),
            ("dwMaximumWindowSize", _Coord),
        ]

    _kernel32 = ctypes.windll.kernel32
    _kernel32.GetStdHandle.restype = ctypes.c_void_p

    def _console_row() -> int | None:
        """`cursor_row`'s Windows half: GetConsoleScreenBufferInfo gives
        the cursor and the visible window in buffer coordinates; their
        difference is the CPR row. The call itself is the console test —
        it fails when stdout is a pipe or a file, and None keeps the
        POSIX contract."""
        info = _ScreenBufferInfo()
        handle = _kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if not _kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return None
        return int(info.dwCursorPosition.Y) - int(info.srWindow.Top) + 1
