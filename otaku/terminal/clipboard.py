"""Reading the system clipboard through the platform's own tool.

Ctrl+V is otaku's own key, not the terminal's: a terminal paste (Cmd+V,
Ctrl+Shift+V) arrives as text on stdin and needs nobody's help, but the
plain Ctrl+V a keyboard sends is just a control byte — the app has to go
and fetch the clipboard itself. No dependency for it: every platform
ships a command that prints the clipboard, and a machine without one
simply pastes nothing.
"""

import subprocess
import sys

# Tried in order, per platform; the first that runs wins. X11 has two
# common tools and Wayland its own, none of them guaranteed present.
_READERS: dict[str, tuple[list[str], ...]] = {
    "darwin": (["pbpaste"],),
    "win32": (["powershell", "-NoProfile", "-Command", "Get-Clipboard"],),
}
_UNIX_READERS = (
    ["wl-paste", "--no-newline"],
    ["xclip", "-selection", "clipboard", "-o"],
    ["xsel", "--clipboard", "--output"],
)


def paste() -> str:
    """The clipboard's text as one line — a url or an api key is one, and
    a pasted newline is noise. "" when nothing on this machine can read
    it, so the caller pastes nothing rather than failing."""
    for command in _READERS.get(sys.platform, _UNIX_READERS):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=False)
        except (OSError, subprocess.SubprocessError):
            continue  # not installed on this machine — try the next
        if result.returncode == 0:
            return one_line(result.stdout)
    return ""


def one_line(text: str) -> str:
    """Pasted text flattened for a one-line field: newlines dropped, the
    edges trimmed. Shared with the bracketed-paste path, so a clipboard
    read and a terminal paste land identically."""
    return text.replace("\r", "").replace("\n", "").strip()
