"""A status line pinned to the bottom row while the model streams.

prompt_toolkit's bottom toolbar only exists while its prompt application
is running, so it vanishes the moment you submit — which is exactly when
the background worker is most likely to still be running (a pass in flight
is no longer cancelled by submitting). This keeps the same line visible
across that gap.

The mechanism is the one `apt` uses: DECSTBM. Setting the terminal's
scroll region to rows 1..H-1 means everything printed scrolls *within*
those rows and never touches row H, so the status sits there untouched
with no cursor arithmetic per chunk and no interference with wrapped or
styled output.

Deliberately scoped to the streaming window rather than the whole session:
prompt_toolkit sizes its layout from the full terminal height and would
happily draw over a reserved row. Reserving only while prompt_toolkit is
NOT running means the two never overlap — and because both sit on the
bottom row, the line looks continuous across the handoff.

Everything is a no-op off a TTY, so piped and redirected output stays
clean.
"""

import atexit
import contextlib
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import TextIO

from otaku.formatting import truncate
from otaku.terminal.tty import (
    DIM,
    ERASE_LINE,
    GOTO_ROW,
    INDEX,
    RESET,
    RESTORE_CURSOR,
    SAVE_CURSOR,
    SCROLL_ABOVE,
    SCROLL_ALL,
    UP_ONE,
)


def _terminal() -> TextIO:
    """The real terminal stream — past every `sys.stdout` wrapper. A
    repaint is not command output: written through a dispatch window's
    wrapper it would fire the armed lead blank or invalidate the screen
    ledger from the repaint thread (the same rule terminal queries
    follow)."""
    return sys.__stdout__ if sys.__stdout__ is not None else sys.stdout


LABEL = " background task: "
# How long a note stands at least once shown: a call that ends at once —
# a tool otaku runs in no time — still reads.
_NOTE_SECONDS = 1.0


def render(status: str, aside: str = "", width: int = 0, note: str = "") -> str:
    """The line's text: `note`, what the reply streaming is doing — a tool
    call — then what the background worker is doing, either blank when
    idle, and `aside` against the right edge of `width` columns, the
    words on the left giving way where both do not fit. The last column
    stays empty, so the row never wraps. ONE owner, because the prompt
    toolbar and this pinned row draw the same line on the same terminal
    row, and any difference between them shows up as the line twitching
    the moment a reply starts streaming."""
    worker = f"{LABEL}{status}" if status else ""
    left = f" {note}" + (f" ·{worker}" if worker else "") if note else worker
    room = width - 1
    if not aside or room <= len(aside):
        return left[: max(0, room)] if width else left
    space = room - len(aside)
    if len(left) > space - 2:
        left = truncate(left, max(0, space - 2))
    return left + " " * (space - len(left)) + aside


class StatusLine:
    """Reserves the bottom terminal row and paints `read()` into it, with
    `aside()` against its right edge. Long-lived: the REPL builds one per
    session; `pinned()` holds the row for a streaming window, and
    `refresh()` (safe from the worker thread) is a no-op whenever the row
    isn't held."""

    def __init__(self, read: Callable[[], str], aside: Callable[[], str]) -> None:
        self._read = read
        self._aside = aside
        self._note = ""  # what the reply streaming is doing, while it streams
        self._noted_at = 0.0  # when the note standing was shown
        self._clearing: threading.Timer | None = None  # a clear waiting out the minimum
        self._lock = threading.Lock()
        self._rows = 0
        self._open = False

    @contextlib.contextmanager
    def pinned(self) -> Iterator[None]:
        """Hold the status line on the bottom row for the block. A no-op
        off a TTY. The region is always released, including on Ctrl+C — a
        stray region would leave the terminal scrolling in a short
        window."""
        if not _terminal().isatty() or os.environ.get("TERM") == "dumb":
            yield
            return
        self.open()
        try:
            yield
        finally:
            self.close()

    def open(self) -> None:
        with self._lock:
            if self._open:
                return
            self._rows = shutil.get_terminal_size().lines
            if self._rows < 3:  # nothing to reserve on a 2-row terminal
                return
            # The index guarantees a free bottom row: if the cursor was
            # already on the last one, the screen scrolls instead of the
            # status overwriting live text. Stepping back up afterwards
            # leaves the cursor exactly where it started — the COLUMN
            # too, which a newline would not: the tty adds a return to
            # it, and on the canvas the cursor stands mid-line here, where
            # the reply is about to go on.
            region = SCROLL_ABOVE.format(self._rows - 1)
            _terminal().write(f"{INDEX}{SAVE_CURSOR}{region}{RESTORE_CURSOR}{UP_ONE}")
            self._open = True
            atexit.register(self.close)
        self.refresh()

    def close(self) -> None:
        with self._lock:
            self._note = ""  # the reply's own doing ends with its window
            self._cancel_clearing()
            if not self._open:
                return
            self._open = False
            # Clear the reserved row, then release the region. Both move
            # the cursor, so both are wrapped in save/restore.
            _terminal().write(
                f"{SAVE_CURSOR}{GOTO_ROW.format(self._rows)}{ERASE_LINE}{RESTORE_CURSOR}"
                f"{SAVE_CURSOR}{SCROLL_ALL}{RESTORE_CURSOR}"
            )
            _terminal().flush()
        atexit.unregister(self.close)

    def set_note(self, note: str) -> None:
        """Say what the reply streaming is doing — a tool being called —
        ahead of the worker's words, "" for nothing; repainted when it
        changes. A note stands `_NOTE_SECONDS` at least: cleared sooner,
        it goes when they are out, on a timer, the stream never held; a
        new note takes over at once, and the row's release clears it."""
        with self._lock:
            self._cancel_clearing()
            if note == self._note:
                return
            if not note:
                left = self._noted_at + _NOTE_SECONDS - time.monotonic()
                if left > 0:
                    self._clearing = threading.Timer(left, self._clear_note)
                    self._clearing.daemon = True
                    self._clearing.start()
                    return
            else:
                self._noted_at = time.monotonic()
            self._note = note
        self.refresh()

    def _clear_note(self) -> None:
        """The waiting clear, on its timer's thread — unless a note set
        since, or the row's release, took it over."""
        with self._lock:
            if self._clearing is None or self._clearing is not threading.current_thread():
                return
            self._clearing = None
            self._note = ""
        self.refresh()

    def _cancel_clearing(self) -> None:
        """Drop a waiting clear; the caller holds the lock."""
        if self._clearing is not None:
            self._clearing.cancel()
            self._clearing = None

    def refresh(self) -> None:
        """Repaint. Safe to call from the worker thread, and a no-op when
        the row isn't held — the same callback serves the prompt toolbar."""
        with self._lock:
            if not self._open:
                return
            size = shutil.get_terminal_size()
            if size.lines != self._rows and size.lines >= 3:
                # Resized under us: re-reserve against the new height.
                self._rows = size.lines
                _terminal().write(
                    f"{SAVE_CURSOR}{SCROLL_ABOVE.format(self._rows - 1)}{RESTORE_CURSOR}"
                )
            body = render(self._read(), self._aside(), size.columns, self._note)
            _terminal().write(
                f"{SAVE_CURSOR}{GOTO_ROW.format(self._rows)}{ERASE_LINE}{DIM}{body}{RESET}{RESTORE_CURSOR}"
            )
            _terminal().flush()
