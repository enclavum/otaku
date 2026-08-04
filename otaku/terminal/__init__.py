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

# SGR text attributes
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
RESET = "\x1b[0m"
DEFAULT_BG = "\x1b[49m"  # back to the terminal's own background

# Erasing and cursor motion
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

# A played user turn, echoed: its text on a light band (#f0f0f0), the
# text in the terminal's normal color.
_USER_TURN = "\x1b[48;2;240;240;240m"


def user_block(text: str) -> str:
    """`text` as the submitted-turn block: every line on the band behind a
    `> ` marker echoing the prompt. The band runs the full terminal
    width — erase-to-end-of-line with the background active paints the
    rest of the row, so no width math is needed. Printed between blank
    lines by the callers."""
    lines = text.splitlines() or [""]
    return "\n".join(f"{_USER_TURN}{PROMPT_PREFIX}{line}\x1b[K{RESET}" for line in lines)


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
