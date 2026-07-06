"""Copy text to the system clipboard.

Tries the platform's native clipboard tool first (``pbcopy`` on macOS,
``wl-copy`` / ``xclip`` / ``xsel`` on Linux, ``clip`` / ``clip.exe`` on
Windows/WSL), then falls back to the **OSC 52** terminal escape when none is
available or the tool fails. OSC 52 needs no external program and reaches the
*local* terminal's clipboard even over SSH, so ``copy`` never hard-fails with an
availability error — it just degrades.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys

_TIMEOUT = 5.0


def _native_candidates() -> list[tuple[str, list[str]]]:
    """(label, argv) clipboard commands to try, in preference order for the
    current platform."""
    if sys.platform == "darwin":
        return [("pbcopy", ["pbcopy"])]
    if sys.platform == "win32":
        return [("clip", ["clip"])]
    # Linux/BSD: Wayland first, then X11, then WSL's clip.exe.
    return [
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
        ("clip.exe", ["clip.exe"]),
    ]


def _osc52(text: str) -> None:
    """Write the OSC 52 clipboard-set escape for `text` to stdout."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    sys.stdout.write(f"\x1b]52;c;{payload}\x07")
    sys.stdout.flush()


def copy(text: str) -> str:
    """Copy `text` to the clipboard and return the method used — a native tool's
    label (e.g. ``"pbcopy"``) or ``"osc52"`` when it fell back to the escape."""
    data = text.encode("utf-8")
    for label, argv in _native_candidates():
        if shutil.which(argv[0]) is None:
            continue
        try:
            subprocess.run(
                argv,
                input=data,
                check=True,
                timeout=_TIMEOUT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return label
        except (OSError, subprocess.SubprocessError):
            continue  # installed but failed (e.g. xclip with no DISPLAY) → next
    _osc52(text)
    return "osc52"
