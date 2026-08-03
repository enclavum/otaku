"""Ask the terminal where its cursor is (DSR 6 → CPR).

The one ground truth the screen-erasing paths need: how many rows sit
above the cursor. The report is asked of the terminal itself, so scroll
regions, prompt_toolkit's menu scrolling, and everything else that moved
the screen are already accounted for.
"""

import contextlib
import os
import re
import select
import sys
import time

# POSIX-only raw-terminal control, exactly like the in-stream watcher's:
# absent on Windows, where the query degrades to "no answer".
try:
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

_QUERY = "\x1b[6n"
_RESPONSE = re.compile(rb"\x1b\[\??(\d+);\d+R")
_DEADLINE = 0.2


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
