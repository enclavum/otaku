"""The live tail under the `otaku web` banner.

Once the browser has the session, the terminal that started it has one
job left: showing that something is reaching the server at all. So the
last few requests stay on screen and are REWRITTEN in place — the
terminal is the same height after an hour as after a minute.

Two rules keep it calm enough to leave running. Only what the reader
ASKED for arrives here — the caller decides that, and a page load is
thirty assets, none of them news. And a repeat of the line already at
the bottom counts up instead of scrolling the list, which is what makes
a once-a-second poll cost one row rather than six hundred.

Nothing is drawn where stdout is not a terminal: this display takes its
own lines back, and a pipe cannot give them.

For as long as it runs it owns the terminal, which is why it is entered
and left rather than merely made: inside, Ctrl+C arrives without the tty
echoing a `^C` into the middle of the sentence the app answers it with.
That posture (`quiet_ctrl_c`, at the foot of this module) has no other
caller — a frontend that answers the key itself is a frontend with a
tail to keep still.
"""

import contextlib
import os
import shutil
import sys
import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from types import TracebackType

from otaku.console import DIM, ERASE_BELOW, RESET, UP
from otaku.formatting import truncate

# POSIX-only raw-terminal control: absent on Windows, where the quiet
# below is simply not needed — nothing echoes a `^C` there.
try:
    import termios
except ImportError:
    termios = None  # type: ignore[assignment]

__all__ = ["Ticker"]

# How many rows the tail keeps. Five is enough to see a page load's
# shape (the document, the session, the table, the turns) without the
# terminal becoming a log.
_KEEP = 5

_CLOCK_WIDTH = 8  # HH:MM:SS


class Ticker:
    """The last few lines, redrawn in place under the banner.

    Thread-safe by construction: the requests arrive on the server's own
    threads, and two landing together must not interleave inside one
    redraw.
    """

    def __init__(self, keep: int = _KEEP) -> None:
        self._keep = keep
        # Newest last: the clock it arrived at, its text, how many times
        # it has arrived running.
        self._rows: list[tuple[str, str, int]] = []
        self._drawn = 0
        self._lock = threading.Lock()
        self._live = sys.stdout.isatty()
        self._dim = "" if os.environ.get("NO_COLOR") else DIM
        self._reset = "" if os.environ.get("NO_COLOR") else RESET
        self._quiet = contextlib.ExitStack()
        self._restore: Callable[[], None] = lambda: None

    def __enter__(self) -> "Ticker":
        self._restore = self._quiet.enter_context(quiet_ctrl_c())
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()
        self._quiet.close()

    def stop(self) -> None:
        """Draw no more, and give the terminal its own behaviour back.

        The rows already on screen stay where they are, and whatever is
        printed next has the terminal to itself — a tail that redrew
        after it would erase it, since a redraw takes back the lines
        below the cursor. Called the moment the reader asks for the
        door, not only on the way out: the NEXT Ctrl+C is the fatal one,
        and a process that dies on the default handler never reaches an
        `__exit__`."""
        self._live = False
        self._restore()

    def show(self, text: str) -> None:
        """One line to show. Any thread; nothing is drawn off a pipe."""
        if not self._live:
            return
        # Nothing in it the terminal would obey: what arrives here has
        # been off a socket — a page anywhere can ask a loopback server
        # for a path of escape sequences — and a line that can move the
        # cursor is a line that can rewrite the address above it.
        text = "".join(char if char.isprintable() else "·" for char in text)
        with self._lock:
            clock = datetime.now().astimezone().strftime("%H:%M:%S")
            if self._rows and self._rows[-1][1] == text:
                _, _, times = self._rows[-1]
                self._rows[-1] = (clock, text, times + 1)
            else:
                self._rows.append((clock, text, 1))
                del self._rows[: -self._keep]
            self._draw()

    def _draw(self) -> None:
        """The whole tail, over the top of the last one."""
        out = []
        if self._drawn:
            out.append(UP.format(self._drawn))
        # From the cursor down, not row by row: the tail is one block and
        # a shorter redraw must not leave the old bottom row behind.
        out.append(ERASE_BELOW)
        room = shutil.get_terminal_size((80, 24)).columns - _CLOCK_WIDTH - 2
        for clock, text, times in self._rows:
            line = text if times == 1 else f"{text} x{times}"
            out.append(f"{self._dim}{clock}  {truncate(line, room)}{self._reset}\n")
        self._drawn = len(self._rows)
        sys.stdout.write("".join(out))
        sys.stdout.flush()


@contextlib.contextmanager
def quiet_ctrl_c() -> "Iterator[Callable[[], None]]":
    """Ctrl+C without the `^C`. The tty driver echoes control characters
    on its own (ECHOCTL); an app that answers the key itself says so in
    words, and the stray caret lands in the middle of them.

    Yields the restore, idempotent, for the app to call the moment it
    stops needing the quiet — a second Ctrl+C is usually the fatal one,
    and a process that dies on the default handler never reaches the
    `finally` below. A no-op where there is no tty to set (a pipe,
    Windows)."""
    fd = None
    saved = None
    if termios is not None:
        with contextlib.suppress(ValueError, OSError, termios.error):
            fd = sys.stdin.fileno()
            if os.isatty(fd):
                saved = termios.tcgetattr(fd)
    if fd is None or saved is None:
        yield lambda: None
        return

    def restore() -> None:
        with contextlib.suppress(ValueError, OSError, termios.error):
            termios.tcsetattr(fd, termios.TCSANOW, saved)

    try:
        quiet = list(saved)
        quiet[3] = int(quiet[3]) & ~termios.ECHOCTL  # lflags
        termios.tcsetattr(fd, termios.TCSANOW, quiet)
        yield restore
    finally:
        restore()
