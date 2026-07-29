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

# Clipboard
CLIPBOARD_COPY = "\x1b]52;c;{}\x07"  # OSC 52: set the clipboard to the base64 payload

# Confirm-prompt answers, matched after `latin_key` folds the typed layout.
# A site with a yes-default accepts the empty answer explicitly.
YES_ANSWERS = {"y", "yes"}
NO_ANSWERS = {"n", "no"}

# ЙЦУКЕН → QWERTY, row by row, by physical position.
_RUSSIAN_TO_LATIN = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбюё",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,.`",
)


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
