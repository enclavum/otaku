"""Questions asked of the terminal itself.

One posture: take the tty raw for a moment, write the query, read until
the answer or a short deadline, always restore — and answer None when
the terminal keeps it to itself (a pipe, a silent emulator), so callers
fall back instead of stalling. Bytes typed while a query is in flight
are read with the response and dropped: the window is a few
milliseconds, and a swallowed keystroke costs one re-press, while
preserving it would cost a screen model.

`cursor_row` (DSR 6 → CPR) is the ground truth the screen-erasing paths
need: how many rows sit above the cursor, with scroll regions and menu
scrolling already accounted for.
"""

import contextlib
import os
import re
import select
import sys
import time

# POSIX-only raw-terminal control, exactly like the in-stream watcher's:
# absent on Windows, where every query degrades to "no answer".
try:
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

_CPR = re.compile(rb"\x1b\[\??(\d+);\d+R")
_DEADLINE = 0.2


def cursor_row() -> int | None:
    """The cursor's 1-based screen row, or None when it cannot be known.
    The terminal must be in cooked mode (between prompts, after a
    stream)."""
    match = _ask("\x1b[6n", _CPR)
    return int(match.group(1)) if match else None


def _ask(query: str, response: re.Pattern[bytes]) -> re.Match[bytes] | None:
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
        sys.stdout.write(query)
        sys.stdout.flush()
        return _read(fd, response)
    except OSError:
        return None
    finally:
        with contextlib.suppress(termios.error):
            termios.tcsetattr(fd, termios.TCSANOW, orig)


def _read(fd: int, response: re.Pattern[bytes]) -> re.Match[bytes] | None:
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
        match = response.search(data)
        if match:
            return match
