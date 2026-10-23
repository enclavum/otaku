"""Self-update: how this otaku was installed decides how it updates.

`upgrade_command` reads the running install — Homebrew's Cellar, uv's
tools dir, pipx's venvs, a git checkout — and hands back that
installer's own upgrade invocation (None for a checkout: git updates
it, not otaku); `run` executes it. The cli's `otaku update` narrates
around these. `fetch_latest_version` reads the version announced as
current, which the cli hands the app for its update check
(`App.check_for_update`).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

import otaku

# Each installer's own upgrade, by install kind; anything unrecognized
# falls back to pip in the running interpreter.
_UPGRADES = {
    "brew": ["brew", "upgrade", "enclavum/tap/otaku"],
    "uv": ["uv", "tool", "upgrade", "otaku"],
    "pipx": ["pipx", "upgrade", "otaku"],
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
_UPDATE_CHECK_URL = (
    f"https://raw.githubusercontent.com/enclavum/otaku/{_UPDATE_CHECK_TAG}/otaku/__init__.py"
)
# For otaku's own tests, which run the real binary — unadvertised.
_UPDATE_CHECK_URL_VAR = "OTAKU_UPDATE_URL"
_ASK_SECONDS = 5.0
_VERSION_LINE = re.compile(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"', re.MULTILINE)


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
    found = _VERSION_LINE.search(reply.text)
    return found.group(1) if found else None


def upgrade_command() -> list[str] | None:
    """The upgrade command for this very install — None for a source
    checkout, which git updates, not otaku."""
    source = (Path(otaku.__file__).resolve().parents[1] / ".git").exists()
    kind = install_kind(Path(sys.prefix), source=source)
    if kind == "source":
        return None
    return _UPGRADES.get(kind) or [sys.executable, "-m", "pip", "install", "--upgrade", "otaku"]


def run(command: list[str]) -> int:
    """Run the upgrade, its output streaming straight through; the exit
    code, a missing installer binary counting as failure."""
    try:
        return subprocess.run(command).returncode
    except FileNotFoundError:
        return 1


def install_kind(prefix: Path, *, source: bool) -> str:
    """How this otaku got onto the machine, read off where it runs:
    "source" (a git checkout, updated by git wherever its venv lives),
    "brew" (Homebrew's Cellar), "uv" (its tools dir), "pipx" (its
    venvs) — or "pip" for every other environment."""
    if source:
        return "source"
    parts = prefix.parts
    if "Cellar" in parts:
        return "brew"
    if "uv" in parts and "tools" in parts:
        return "uv"
    if "pipx" in parts:
        return "pipx"
    return "pip"
