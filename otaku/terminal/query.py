"""Questions asked of the terminal itself.

Two of them, one posture: take the tty raw for a moment, write the query,
read until the answer or a short deadline, always restore — and answer
None when the terminal keeps it to itself (a pipe, a silent emulator), so
callers fall back instead of stalling. Bytes typed while a query is in
flight are read with the response and dropped: the window is a few
milliseconds, and a swallowed keystroke costs one re-press, while
preserving it would cost a screen model.

`cursor_row` (DSR 6 → CPR) is the ground truth the screen-erasing paths
need: how many rows sit above the cursor, with scroll regions and menu
scrolling already accounted for. `background_is_dark` (COLORFGBG, then
OSC 11) is what adaptive colors need — asked once and cached, because a
background does not change mid-session.
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
_OSC11 = re.compile(rb"\x1b\]11;rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)")
_DEADLINE = 0.2

# background_is_dark's one-per-session answer; a list so "not asked yet"
# and "asked, unanswered" stay distinct.
_BACKGROUND: list[bool | None] = []


def cursor_row() -> int | None:
    """The cursor's 1-based screen row, or None when it cannot be known.
    The terminal must be in cooked mode (between prompts, after a
    stream)."""
    match = _ask("\x1b[6n", _CPR)
    return int(match.group(1)) if match else None


def background_is_dark() -> bool | None:
    """Whether the terminal background is dark: COLORFGBG when a terminal
    exports it, the OSC 11 report otherwise; None when neither answers.
    Asked once — the answer, None included, is cached for the session."""
    if not _BACKGROUND:
        _BACKGROUND.append(_probe_background())
    return _BACKGROUND[0]


def _probe_background() -> bool | None:
    dark = _dark_from_colorfgbg(os.environ.get("COLORFGBG", ""))
    if dark is not None:
        return dark
    match = _ask("\x1b]11;?\x07", _OSC11)
    if match is None:
        return None
    r, g, b = (_channel(part) for part in match.groups())
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5


def _dark_from_colorfgbg(value: str) -> bool | None:
    """COLORFGBG is "fg;bg" (sometimes "fg;default;bg"): the last field is
    the background's palette slot — 7 and 15 are the light backgrounds,
    every other slot is dark. Unset or unreadable: None."""
    slot = value.strip().rsplit(";", 1)[-1]
    if not slot.isdigit():
        return None
    return int(slot) not in (7, 15)


def _channel(part: bytes) -> float:
    """One OSC 11 color component ("1c1c", scale set by its width) → 0..1."""
    return int(part, 16) / (16 ** len(part) - 1)  # type: ignore[no-any-return]


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
