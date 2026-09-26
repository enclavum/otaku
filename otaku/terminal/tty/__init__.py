"""The medium itself — drawing and reading the terminal, with no
knowledge of the chat package. This module is the escape vocabulary
(every sequence otaku prints, spelled once: plain constants for the
fixed ones, `str.format` templates for those parameterized by a row
number) and the input-side helpers — `latin_key`, the confirm answers,
and `ask`, the raw-tty question posture the real-terminal reads share;
the siblings own the theme, the typesetter, row math (and the cursor's
one real ask), the pinned row, the spinner, and the clipboard. The banner
a session opens with and the notification sound are not here: both
frontends draw and ring them, so they live below (`otaku.console`). `render`
is the one member that knows the story's types (how a turn looks),
placed here so prompt and screens can reach it from below chat.
"""

import contextlib
import os
import re
import select
import sys
import time

# POSIX-only raw-terminal control: absent on Windows, where every query
# degrades to "no answer" and callers fall back.
try:
    import termios
    import tty as _tty
except ImportError:
    termios = None  # type: ignore[assignment]
    _tty = None  # type: ignore[assignment]

# SGR text attributes. The console spells three of these for itself —
# the banner and the tail draw with the same ink — and a one-line
# constant is not worth an import between two packages that otherwise
# share nothing.
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
RESET = "\x1b[0m"

# Erasing and cursor motion
CLEAR_SCREEN = "\x1b[H\x1b[2J"  # wipe the visible screen, cursor home; scrollback stays
ERASE_LINE = "\x1b[2K"  # clear the row the cursor is on
UP_ONE = "\x1b[1A"
GOTO_ROW = "\x1b[{};1H"  # CUP to column 1 of the given row
SAVE_CURSOR = "\x1b7"  # DECSC
RESTORE_CURSOR = "\x1b8"  # DECRC

# Modes and regions
SCROLL_ABOVE = "\x1b[1;{}r"  # DECSTBM: scrolling confined to rows 1..N
SCROLL_ALL = "\x1b[r"  # DECSTBM reset: the whole screen scrolls again
CURSOR_BLINK_ON = "\x1b[?12h"  # DECSET 12: ask the terminal to blink the cursor

# Confirm-prompt answers, matched after `latin_key` folds the typed layout.
# A site with a yes-default accepts the empty answer explicitly.
YES_ANSWERS = {"y", "yes"}
NO_ANSWERS = {"n", "no"}

# ЙЦУКЕН → QWERTY, row by row, by physical position: single-key commands
# ("y", "e", "l") compared through `latin_key` just work on the Russian
# layout without ever being announced — help text still says "y".
# Control combos (Ctrl+S) need no folding: the terminal derives the
# control byte from the physical key, the same in any layout.
_RUSSIAN_TO_LATIN = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбюё",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,.`",
)

# The prompt markers: `PROMPT_PREFIX` opens every input line (and each
# line `user_block` echoes); `PROMPT_CONTINUATION` marks the lines of an
# open `\"\"\"` block. The cloud marker says the story is billed by the
# token from here — same width, so nothing else moves.
PROMPT_PREFIX = "> "
PROMPT_CONTINUATION = "... "
CLOUD_PROMPT_PREFIX = "$ "

# The rule the chat screen draws where the played sequence stops
# continuing. A fine dotted line in the terminal's own text color,
# DIMMED — reduced intensity rather than a grey guessed against an
# unknown background, the same treatment secondary text gets (see
# `theme.Theme`). At full intensity the row reads as a hairline on a
# light background and blooms on a dark one, which is the whole reason
# this is not left to the character alone.
_RULE_CHAR = "┈"

_ASK_DEADLINE = 0.2


def user_block(text: str) -> str:
    """`text` as the submitted-turn block: every line on the theme's band
    behind a `> ` marker echoing the prompt. The band runs the full
    terminal width — erase-to-end-of-line with the background active
    paints the rest of the row, so no width math is needed. Printed
    between blank lines by the callers."""
    from otaku.terminal.tty.theme import color, theme  # child of this package

    colors = theme()
    # A span inside `text` that ended by returning to the DEFAULT
    # foreground is returning to the TERMINAL's, not the band's — put the
    # band's back, or a highlighted command leaves the rest of its line
    # unreadable.
    painted = text.replace(color("default").fg, colors.ink.fg)
    band = colors.band.bg + colors.ink.fg
    lines = painted.splitlines() or [""]
    return "\n".join(f"{band}{PROMPT_PREFIX}{line}\x1b[K{RESET}" for line in lines)


def break_rule(width: int) -> str:
    """The break rule, `width` columns wide — one row, printed by the
    caller (the ledger, which decides where a break falls)."""
    return f"{DIM}{_RULE_CHAR * width}{RESET}"


def error_line(text: str) -> str:
    """A failure, in the theme's error color. Only what actually BROKE —
    a provider that refused, a file that would not open, a command that
    raised. A refusal the app expected ("Unknown command", "Nothing to
    regenerate") is not one of these: it is the app answering, and
    coloring it would make an ordinary typo look like a fault."""
    from otaku.terminal.tty.theme import theme  # child of this package

    return f"{theme().error.fg}{text}{RESET}"


def ask(query: str, response: "re.Pattern[bytes]") -> "re.Match[bytes] | None":
    """One question to the terminal itself: take the tty raw for a
    moment, write `query`, read until `response` matches or a short
    deadline, always restore — None when the terminal keeps it to itself
    (a pipe, a silent emulator), so callers fall back instead of
    stalling. Bytes typed while a query is in flight are read with the
    response and dropped: the window is a few milliseconds, and a
    swallowed keystroke costs one re-press, while preserving it would
    cost a screen model."""
    if termios is None or _tty is None:
        return None
    try:
        fd = sys.stdin.fileno()
    except (ValueError, OSError):
        return None
    if not os.isatty(fd) or not sys.stdout.isatty():
        return None
    try:
        orig = termios.tcgetattr(fd)
        _tty.setcbreak(fd)
    except termios.error:
        return None
    try:
        # Straight to the fd, past every sys.stdout wrapper: a query is
        # terminal I/O, not output — decorated (a dispatch window's lead
        # blank) or counted (an output tracker), it would move the very
        # cursor the caller is about to measure from.
        os.write(sys.stdout.fileno(), query.encode())
        return _read(fd, response)
    except OSError:
        return None
    finally:
        with contextlib.suppress(termios.error):
            termios.tcsetattr(fd, termios.TCSANOW, orig)


def latin_key(key: str) -> str:
    """The Latin character(s) on `key`'s physical keys: Cyrillic letters
    map to their QWERTY twins, everything else comes back lowercased as
    is — works on a single keystroke and on a whole typed answer alike."""
    return key.lower().translate(_RUSSIAN_TO_LATIN)


def _read(fd: int, response: "re.Pattern[bytes]") -> "re.Match[bytes] | None":
    data = b""
    deadline = time.monotonic() + _ASK_DEADLINE
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
