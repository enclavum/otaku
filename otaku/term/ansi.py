"""Every ANSI escape sequence otaku prints — one owner, each spelled once.

Plain constants for the fixed sequences, `str.format` templates for the
ones parameterized by a row number, and `fg`/`bg` for 256-color codes.
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


def fg(color: int) -> str:
    """SGR 256-color foreground."""
    return f"\x1b[38;5;{color}m"


def bg(color: int) -> str:
    """SGR 256-color background."""
    return f"\x1b[48;5;{color}m"
