"""Keyboard-layout folding for single-key commands.

The pickers and confirm prompts read bare letter keys ("y", "e", "l"). On
the Russian (ЙЦУКЕН) layout the same physical key delivers the Cyrillic
letter at that position, so every single-key comparison goes through
`latin_key` instead of matching the letter directly. The folding is never
announced: help text says "y" and both layouts just work. Control combos
(Ctrl+S) need no folding — the terminal derives the control byte from the
physical key, the same in any layout.
"""

# ЙЦУКЕН → QWERTY, row by row, by physical position.
_RUSSIAN_TO_LATIN = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбюё",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,.`",
)


def latin_key(key: str) -> str:
    """The Latin character(s) on `key`'s physical keys: Cyrillic letters map
    to their QWERTY twins, everything else comes back lowercased as is —
    works on a single keystroke and on a whole typed answer alike."""
    return key.lower().translate(_RUSSIAN_TO_LATIN)
