"""This otaku on the machine: where it runs from and how it got there
(`checkout_folder`, `install_kind`), the commands that update and
uninstall it — its own installer's, by how it was installed
(`upgrade_command`, `uninstall_command`; None for a git checkout, which
git updates and removes, not otaku), run by `run` — and the update check
(`fetch_latest_version`), the version announced as current, which the
cli hands the app (`App.check_for_update`). The cli's `otaku update` and
`otaku uninstall` narrate around these; the home folder's default goes by
the checkout too (`backend.app.paths`).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

# Each installer's own upgrade, by install kind; anything unrecognized
# falls back to pip in the running interpreter.
_UPGRADES = {
    "brew": ["brew", "upgrade", "enclavum/tap/otaku"],
    "uv": ["uv", "tool", "upgrade", "otaku"],
    "pipx": ["pipx", "upgrade", "otaku"],
}

# Each installer's own uninstall, by install kind; anything unrecognized
# falls back to pip in the running interpreter.
_UNINSTALLS = {
    "brew": ["brew", "uninstall", "enclavum/tap/otaku"],
    "uv": ["uv", "tool", "uninstall", "otaku"],
    "pipx": ["pipx", "uninstall", "otaku"],
}

# What to run by hand when the automatic path fails.
MANUAL_COMMANDS = (
    "brew upgrade enclavum/tap/otaku",
    "uv tool upgrade otaku",
    "pip install --upgrade otaku",
)

# The version announced as current: otaku's own __init__.py at the tag the
# maintainer moves to a release to announce it. Every installed otaku reads
# this path, so the file keeps its place and its `__version__` line its
# shape (`_VERSION_LINE`).
_UPDATE_CHECK_TAG = "stable"
_UPDATE_CHECK_URL = f"https://github.com/enclavum/otaku/blob/{_UPDATE_CHECK_TAG}/otaku/__init__.py"
# For otaku's own tests, which run the real binary — unadvertised.
_UPDATE_CHECK_URL_VAR = "OTAKU_UPDATE_URL"
_ASK_SECONDS = 5.0
_VERSION_LINE = re.compile(r'__version__\s*=\s*\\?"(\d+\.\d+\.\d+)\\?"')


# ---------- this install ----------


def checkout_folder() -> Path | None:
    """The folder of the git checkout this otaku runs from — the one
    holding its `.git`, a file in a worktree — or None for an install."""
    folder = Path(__file__).resolve().parents[1]
    return folder if (folder / ".git").exists() else None


def install_kind() -> str:
    """How this otaku got onto the machine, read off where it runs:
    "source" (a git checkout, updated by git wherever its venv lives),
    "brew" (Homebrew's Cellar), "uv" (its tools dir), "pipx" (its
    venvs) — or "pip" for every other environment."""
    if checkout_folder() is not None:
        return "source"
    parts = Path(sys.prefix).parts
    if "Cellar" in parts:
        return "brew"
    if "uv" in parts and "tools" in parts:
        return "uv"
    if "pipx" in parts:
        return "pipx"
    return "pip"


# ---------- updating and uninstalling it ----------


def upgrade_command() -> list[str] | None:
    """The upgrade command for this very install — None for a source
    checkout, which git updates, not otaku."""
    kind = install_kind()
    if kind == "source":
        return None
    return _UPGRADES.get(kind) or [sys.executable, "-m", "pip", "install", "--upgrade", "otaku"]


def uninstall_command() -> list[str] | None:
    """The uninstall command for this very install — None for a source
    checkout, which git removes, not otaku."""
    kind = install_kind()
    if kind == "source":
        return None
    return _UNINSTALLS.get(kind) or [sys.executable, "-m", "pip", "uninstall", "--yes", "otaku"]


def run(command: list[str]) -> int:
    """Run an installer's command, its output streaming straight through;
    the exit code, a missing installer binary counting as failure."""
    try:
        return subprocess.run(command).returncode
    except FileNotFoundError:
        return 1


# ---------- the update check ----------


def fetch_latest_version() -> str | None:
    """The version announced as current — the `X.Y.Z` that otaku/__init__.py
    names at the update check's tag — or None where it cannot be had: no
    network, no tag yet, a file not in shape."""
    url = os.environ.get(_UPDATE_CHECK_URL_VAR, "").strip() or _UPDATE_CHECK_URL
    try:
        reply = httpx.get(url, timeout=_ASK_SECONDS)
    except (httpx.HTTPError, httpx.InvalidURL):
        return None
    if reply.status_code != 200:
        return None
    text = re.sub(r"<[^>]*>", "", reply.text).replace("&quot;", '"')
    found = _VERSION_LINE.search(text)
    return found.group(1) if found else None
