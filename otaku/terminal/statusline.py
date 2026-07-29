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
from collections.abc import Callable, Iterator

from otaku.terminal import (
    DIM,
    ERASE_LINE,
    GOTO_ROW,
    RESET,
    RESTORE_CURSOR,
    SAVE_CURSOR,
    SCROLL_ABOVE,
    SCROLL_ALL,
    UP_ONE,
)

LABEL = " background task: "


def render(status: str) -> str:
    """The line's text — blank when idle. ONE owner, because the prompt
    toolbar and this pinned row draw the same line on the same terminal
    row, and any difference between them shows up as the line twitching
    the moment a reply starts streaming."""
    return f"{LABEL}{status}" if status else ""


class StatusLine:
    """Reserves the bottom terminal row and paints `read()` into it.
    Long-lived: the REPL builds one per session; `pinned()` holds the row
    for a streaming window, and `refresh()` (safe from the worker thread)
    is a no-op whenever the row isn't held."""

    def __init__(self, read: Callable[[], str]) -> None:
        self._read = read
        self._lock = threading.Lock()
        self._rows = 0
        self._open = False

    @contextlib.contextmanager
    def pinned(self) -> Iterator[None]:
        """Hold the status line on the bottom row for the block. A no-op
        off a TTY. The region is always released, including on Ctrl+C — a
        stray region would leave the terminal scrolling in a short
        window."""
        if not sys.stdout.isatty() or os.environ.get("TERM") == "dumb":
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
            # The newline guarantees a free bottom row: if the cursor was
            # already on the last one, the screen scrolls instead of the
            # status overwriting live text. Stepping back up afterwards
            # leaves the cursor exactly where it started.
            region = SCROLL_ABOVE.format(self._rows - 1)
            sys.stdout.write(f"\n{SAVE_CURSOR}{region}{RESTORE_CURSOR}{UP_ONE}")
            self._open = True
            atexit.register(self.close)
        self.refresh()

    def close(self) -> None:
        with self._lock:
            if not self._open:
                return
            self._open = False
            # Clear the reserved row, then release the region. Both move
            # the cursor, so both are wrapped in save/restore.
            sys.stdout.write(
                f"{SAVE_CURSOR}{GOTO_ROW.format(self._rows)}{ERASE_LINE}{RESTORE_CURSOR}"
                f"{SAVE_CURSOR}{SCROLL_ALL}{RESTORE_CURSOR}"
            )
            sys.stdout.flush()
        atexit.unregister(self.close)

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
                sys.stdout.write(
                    f"{SAVE_CURSOR}{SCROLL_ABOVE.format(self._rows - 1)}{RESTORE_CURSOR}"
                )
            body = render(self._read())[: max(0, size.columns - 1)]
            sys.stdout.write(
                f"{SAVE_CURSOR}{GOTO_ROW.format(self._rows)}{ERASE_LINE}{DIM}{body}{RESET}{RESTORE_CURSOR}"
            )
            sys.stdout.flush()
