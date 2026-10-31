"""The status row: the one line the terminal keeps on its bottom row —
what the reply streaming is doing or how the last one ended, what the
background worker is doing, the page served beside the chat, and how
full the next request is — composed and painted here alone, for the two
places it is drawn, so the two never differ.

While the prompt is up the row is the prompt's own bottom toolbar
(`fragments`, which the prompt asks for). prompt_toolkit's toolbar only
exists while its prompt application is running, so it vanishes the
moment a line is sent — which is exactly when a reply streams, a command
prints, and the background worker is most likely to still be running (a
pass in flight is no longer cancelled by submitting). For that gap the
same line is pinned to the bottom row (`pinned`).

The mechanism is the one `apt` uses: DECSTBM. Setting the terminal's
scroll region to rows 1..H-1 means everything printed scrolls *within*
those rows and never touches row H, so the status sits there untouched
with no cursor arithmetic per chunk and no interference with wrapped or
styled output.

Deliberately scoped to the windows without a prompt rather than the
whole session: prompt_toolkit sizes its layout from the full terminal
height and would happily draw over a reserved row, and erase it on every
redraw. Reserving only while prompt_toolkit is NOT running means the two
never overlap — and because both sit on the bottom row, the line looks
continuous across the handoff. The row is held past the block that pins
it until the next prompt opens and takes it over (`hand_over`), so the
handoff leaves no gap: the prompt's own first draw is what erases it.

The pinned row is a no-op off a TTY, so piped and redirected output stays
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

from prompt_toolkit.formatted_text import StyleAndTextTuples

from otaku.console.banner import link_ends
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
from otaku.terminal.tty.theme import theme

_LABEL = " background task: "
# The mark of the page served beside the chat: the dot, painted in the
# theme's error color, and the label after it, a link to the page where
# the terminal makes links.
_SERVING_DOT = "●"
_SERVING_LABEL = "web server on ↗"
# The width the mark stands in, blank where no page is served.
_SERVING_WIDTH = len(f"{_SERVING_DOT} {_SERVING_LABEL}")
# The width the context size stands in ("999% of 128K"), so what stands
# left of it does not move as it changes.
_FILL_WIDTH = 12
# A piece's paint where it is the link's end around the label: no width,
# no paint, written as it is.
_LINK = "link"

# How long a note stands at least once shown: a call that ends at once —
# a tool otaku runs in no time — still reads.
_NOTE_SECONDS = 1.0


class StatusLine:
    """The status row over the facts it shows, each read when it is drawn:
    `worker`, the background worker's line; `fill`, how full the next
    request is ("85% of 128K", "" while unknown); `served_at`, where the
    page beside the chat is served ("" for nowhere); `last_note`, what the
    last reply ended on, under the prompt in story mode. Long-lived: the
    REPL builds one per session. The prompt's toolbar draws `fragments`;
    `pinned()` holds the bottom row while no prompt runs and `hand_over()`
    gives it to the prompt opening, `set_note` says what the reply
    streaming is doing there, and `refresh()` (safe from the worker
    thread) is a no-op whenever the row isn't held."""

    def __init__(
        self,
        *,
        worker: Callable[[], str],
        fill: Callable[[], str],
        served_at: Callable[[], str],
        last_note: Callable[[], str],
        waiting_to_wrap: Callable[[], bool],
    ) -> None:
        self._worker = worker
        self._fill = fill
        self._served_at = served_at
        self._last_note = last_note
        self._waiting_to_wrap = waiting_to_wrap
        self._held = False  # a repaint asked for while the cursor waited to wrap
        self._note = ""  # what the reply streaming is doing, while it streams
        self._noted_at = 0.0  # when the note standing was shown
        self._clearing: threading.Timer | None = None  # a clear waiting out the minimum
        self._lock = threading.Lock()
        self._rows = 0
        self._open = False

    def fragments(self) -> StyleAndTextTuples:
        """The row as the prompt's toolbar draws it, the terminal's width
        as the pinned row measures it, the link's ends taking no width —
        prompt_toolkit has no links of its own. The toolbar's row is held
        for the whole prompt, so drawing it never reflows the screen."""
        line = self._line(shutil.get_terminal_size().columns, self._last_note())
        paints = _paints()
        return [
            ("[ZeroWidthEscape]", text) if paint == _LINK else (paints[paint][0], text)
            for paint, text in self._pieces(line)
        ]

    @contextlib.contextmanager
    def pinned(self) -> Iterator[None]:
        """Hold the status line on the bottom row for the block, and on
        past it until the next prompt takes it over (`hand_over`): released
        at the block's end, the row would stand blank until the prompt drew
        its toolbar, and the blank line printed before the prompt would
        open it on the bottom row. A no-op off a TTY. Past a Ctrl+C too the
        region is released by the next prompt, or at exit — a stray region
        would leave the terminal scrolling in a short window."""
        if not _terminal().isatty() or os.environ.get("TERM") == "dumb":
            yield
            return
        self.open()
        try:
            yield
        finally:
            self._drop_note()

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

    def hand_over(self) -> None:
        """Release the row to the prompt opening, before its first draw,
        leaving it painted: that draw erases from the prompt's line down,
        the row with it, in the same write."""
        with self._lock:
            if not self._open:
                return
            self._open = False
            _terminal().write(f"{SAVE_CURSOR}{SCROLL_ALL}{RESTORE_CURSOR}")
            _terminal().flush()
        atexit.unregister(self.close)

    def set_note(self, note: str) -> None:
        """Say what the reply streaming is doing — thinking, a tool being
        called — in the right's first cell, "" for nothing; repainted when
        it changes. A note stands `_NOTE_SECONDS` at least: cleared sooner,
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

    def refresh(self) -> None:
        """Repaint. Safe to call from the worker thread, and a no-op when
        the row isn't held — the same callback serves the prompt toolbar."""
        with self._lock:
            if not self._open:
                return
            if self._waiting_to_wrap():
                # Past a full row's last letter: some terminals forget the
                # wrap waiting there when the cursor is restored, and the
                # next letter lands on it. Painted once the text moves on.
                self._held = True
                return
            self._held = False
            size = shutil.get_terminal_size()
            if size.lines != self._rows and size.lines >= 3:
                # Resized under us: re-reserve against the new height.
                self._rows = size.lines
                _terminal().write(
                    f"{SAVE_CURSOR}{SCROLL_ABOVE.format(self._rows - 1)}{RESTORE_CURSOR}"
                )
            paints = _paints()
            line = "".join(
                text if paint == _LINK else f"{RESET}{paints[paint][1]}{text}"
                for paint, text in self._pieces(self._line(size.columns, ""))
            )
            _terminal().write(
                f"{SAVE_CURSOR}{GOTO_ROW.format(self._rows)}{ERASE_LINE}{line}{RESET}{RESTORE_CURSOR}"
            )
            _terminal().flush()

    def paint_held(self) -> None:
        """The repaint held while the cursor waited to wrap, now it may
        stand elsewhere (`refresh`)."""
        if self._held:
            self.refresh()

    def _line(self, width: int, note: str) -> str:
        return _render(self._worker(), self._right_part(width), width, note)

    def _right_part(self, width: int) -> str:
        """The row's right, three cells set apart by vertical lines, both
        always drawn: what the reply streaming is doing, cut first where
        the row is short; the page served, where it is; how full the next
        request is. The last two keep fixed widths, blank while there is
        nothing to show, so the lines stand still."""
        mark = f"{_SERVING_DOT} {_SERVING_LABEL}" if self._served_at() else ""
        cells = f" | {mark:<{_SERVING_WIDTH}} | {self._fill():>{_FILL_WIDTH}}"
        return truncate(self._note, max(0, width - 2 - len(cells))) + cells

    def _pieces(self, line: str) -> list[tuple[str, str]]:
        """The line cut into its pieces, each with its paint (`_paints`):
        the text, and where the line shows the served mark, its dot and
        the label between its link's ends — the one cut both places the
        row is drawn paint from."""
        url = self._served_at()
        before, dot, rest = line.partition(_SERVING_DOT)
        between, label, after = rest.partition(_SERVING_LABEL)
        if not (url and dot and label):
            return [("text", line)]
        opening, closing = link_ends(url)
        return [
            ("text", before),
            ("dot", _SERVING_DOT),
            ("text", between),
            (_LINK, opening),
            ("text", _SERVING_LABEL),
            (_LINK, closing),
            ("text", after),
        ]

    def _drop_note(self) -> None:
        """End the reply's note with its window, at once."""
        with self._lock:
            self._cancel_clearing()
            if not self._note:
                return
            self._note = ""
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


def _render(status: str, aside: str = "", width: int = 0, note: str = "") -> str:
    """The line's text: `note`, then what the background worker is doing,
    either blank when idle, and `aside` against the right edge of `width`
    columns, the words on the left giving way where both do not fit. The
    last column stays empty, so the row never wraps. ONE owner, because
    the prompt toolbar and the pinned row draw the same line on the same
    terminal row, and any difference between them shows up as the line
    twitching the moment a reply starts streaming."""
    worker = f"{_LABEL}{status}" if status else ""
    left = f" {note}" + (f" ·{worker}" if worker else "") if note else worker
    room = width - 1
    if not aside or room <= len(aside):
        return left[: max(0, room)] if width else left
    space = room - len(aside)
    if len(left) > space - 2:
        left = truncate(left, max(0, space - 2))
    return left + " " * (space - len(left)) + aside


def _paints() -> dict[str, tuple[str, str]]:
    """Each paint of the row in both forms it is drawn in — prompt_toolkit's
    style for the toolbar, the escape for the pinned row — side by side,
    so the two cannot differ: the text dimmed, never a guessed grey
    (`theme`), and the served dot in the theme's error color, undimmed."""
    error = theme().error
    return {"text": ("dim", DIM), "dot": (f"fg:{error.style}", error.fg)}


def _terminal() -> TextIO:
    """The real terminal stream — past every `sys.stdout` wrapper. A
    repaint is not command output: written through a dispatch window's
    wrapper it would fire the armed lead blank or invalidate the screen
    ledger from the repaint thread (the same rule terminal queries
    follow)."""
    return sys.__stdout__ if sys.__stdout__ is not None else sys.stdout
