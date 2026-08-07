"""The terminal vocabulary shared by every surface.

Escape sequences otaku prints — each spelled once: plain constants for the
fixed ones, `str.format` templates for those parameterized by a row
number, and `fg`/`bg` for 256-color codes. Plus the input side: single-key
commands ("y", "e", "l") are compared through `latin_key`, which folds a
Cyrillic letter to the Latin one on the same physical key, so the ЙЦУКЕН
layout just works without ever being announced — help text still says
"y". Control combos (Ctrl+S) need no folding: the terminal derives the
control byte from the physical key, the same in any layout.
"""

import re

from otaku.terminal import query

# SGR text attributes
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
RESET = "\x1b[0m"
DEFAULT_BG = "\x1b[49m"  # back to the terminal's own background

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

# ЙЦУКЕН → QWERTY, row by row, by physical position.
_RUSSIAN_TO_LATIN = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбюё",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,.`",
)

# The prompt markers: `PROMPT_PREFIX` opens every input line (and each
# line `user_block` echoes); `PROMPT_CONTINUATION` marks the lines of an
# open `"""` block.
PROMPT_PREFIX = "> "
PROMPT_CONTINUATION = "... "
# What the marker becomes on a hosted catalog: the story is billed by the
# token from here. Same width as `PROMPT_PREFIX`, so nothing else moves —
# the echoed block keeps its `> ` and the row arithmetic is untouched.
CLOUD_PROMPT_PREFIX = "$ "

# A played user turn, echoed: its text on a band the background picks —
# light grey on a light theme, deep grey on a dark one, the text in the
# terminal's normal color either way. An unanswered background reads as
# light (the shipped look; a pipe has no colors to clash with).
_USER_TURN_LIGHT = "\x1b[48;2;240;240;240m"
_USER_TURN_DARK = "\x1b[48;2;48;48;48m"


def user_block(text: str) -> str:
    """`text` as the submitted-turn block: every line on the band behind a
    `> ` marker echoing the prompt. The band runs the full terminal
    width — erase-to-end-of-line with the background active paints the
    rest of the row, so no width math is needed. Printed between blank
    lines by the callers."""
    band = _USER_TURN_DARK if query.background_is_dark() else _USER_TURN_LIGHT
    lines = text.splitlines() or [""]
    return "\n".join(f"{band}{PROMPT_PREFIX}{line}\x1b[K{RESET}" for line in lines)


# The rule the chat screen draws where the played sequence stops
# continuing. A fine dotted line in the terminal's own text color: the
# character carries the lightness, so the line reads at normal weight and
# stays legible on any theme without a color to shade.
_RULE_CHAR = "┈"


def break_rule(width: int) -> str:
    """The break rule, `width` columns wide — one row, printed by the
    caller (chat/screen.py, which decides where a break falls)."""
    return _RULE_CHAR * width


# Color specs. A NAME is the portable form: it compiles to one of the 16
# palette slots, which every terminal on every platform renders and the
# user's own theme shades, so it stays legible on light and dark alike. A
# #rrggbb is truecolor — exact everywhere, and therefore fixed.
_COLOR_NAMES = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
    "bright black": 90,
    "bright red": 91,
    "bright green": 92,
    "bright yellow": 93,
    "bright blue": 94,
    "bright magenta": 95,
    "bright cyan": 96,
    "bright white": 97,
}
_HEX = re.compile(r"^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$")


def color(spec: str) -> str:
    """A color name ("cyan", "bright blue") or a #rrggbb hex → the SGR
    foreground escape. "" when the spec is neither, so a caller can fall
    back to its default rather than print garbage."""
    text = " ".join(spec.strip().lower().replace("-", " ").replace("_", " ").split())
    slot = _COLOR_NAMES.get(text)
    if slot is not None:
        return f"\x1b[{slot}m"
    m = _HEX.match(spec.strip())
    if m is None:
        return ""
    r, g, b = (int(part, 16) for part in m.groups())
    return f"\x1b[38;2;{r};{g};{b}m"


def fg(color: int) -> str:
    """SGR 256-color foreground."""
    return f"\x1b[38;5;{color}m"


def bg(color: int) -> str:
    """SGR 256-color background."""
    return f"\x1b[48;5;{color}m"


def latin_key(key: str) -> str:
    """The Latin character(s) on `key`'s physical keys: Cyrillic letters map
    to their QWERTY twins, everything else comes back lowercased as is —
    works on a single keystroke and on a whole typed answer alike."""
    return key.lower().translate(_RUSSIAN_TO_LATIN)
