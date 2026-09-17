"""Single keys off the terminal while a frontend owns the screen — what
`otaku web` answers Ctrl+D and Ctrl+R with. The terminal is taken out
of line mode for the watch, so a key arrives as it is pressed rather
than after Enter, and given back after; Ctrl+C stays the signal it is.
Nothing is read where stdin is not a terminal, so a piped launch is
never stopped by its own end-of-file, and nothing is drawn: what a key
does is the caller's.
"""

import contextlib
import os
import select
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping

# POSIX-only: Windows reads keys through msvcrt, with no mode to set.
try:
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

__all__ = ["CTRL_D", "CTRL_R", "watching"]

CTRL_D = b"\x04"
CTRL_R = b"\x12"

_LOOK = 0.2  # seconds between looks, so the watch ends soon after it is asked to


@contextlib.contextmanager
def watching(keys: Mapping[bytes, Callable[[], None]]) -> Iterator[None]:
    """Run a key's callable when it is pressed, until the block ends; a
    key not in the table is dropped, nothing else reading the terminal
    meanwhile. The callable runs on the watch's own thread and must be
    quick. A no-op with nothing to watch for, and off a terminal."""
    if not keys or not _is_terminal():
        yield
        return
    fd = sys.stdin.fileno()
    done = threading.Event()
    with _unlined(fd):
        thread = threading.Thread(target=_watch, args=(fd, keys, done), daemon=True)
        thread.start()
        try:
            yield
        finally:
            done.set()
            thread.join()


def _is_terminal() -> bool:
    with contextlib.suppress(ValueError, OSError, AttributeError):
        return bool(sys.stdin.isatty())
    return False


def _watch(fd: int, keys: Mapping[bytes, Callable[[], None]], done: threading.Event) -> None:
    while not done.is_set():
        key = _pressed(fd)
        act = keys.get(key) if key else None
        if act is not None:
            act()


def _pressed(fd: int) -> bytes:
    """The key pressed within one look, or b"" — the look is what lets
    the watch notice it has been asked to end."""
    if termios is None:
        import msvcrt  # Windows only, and only here

        if msvcrt.kbhit():
            return bytes(msvcrt.getch())
        time.sleep(_LOOK)
        return b""
    ready, _, _ = select.select([fd], [], [], _LOOK)
    return os.read(fd, 1) if ready else b""


@contextlib.contextmanager
def _unlined(fd: int) -> Iterator[None]:
    """The terminal out of line mode for the block: keys arrive as
    pressed, unechoed. Only the two flags and the two counts that
    cbreak changes are put back — not a snapshot — so this composes
    with whoever else restores the terminal meanwhile (the ticker's
    quiet), in whichever order the two let go."""
    if termios is None:
        yield
        return
    before = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError, OSError, termios.error):
            now = termios.tcgetattr(fd)
            now[3] = int(now[3]) | (int(before[3]) & (termios.ICANON | termios.ECHO))
            now[6][termios.VMIN] = before[6][termios.VMIN]
            now[6][termios.VTIME] = before[6][termios.VTIME]
            termios.tcsetattr(fd, termios.TCSADRAIN, now)
