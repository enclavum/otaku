"""Self-update: how this otaku was installed decides how it updates.

`upgrade_command` reads the running install — Homebrew's Cellar, uv's
tools dir, pipx's venvs, a git checkout — and hands back that
installer's own upgrade invocation (None for a checkout: git updates
it, not otaku); `run` executes it. The cli's `otaku update` narrates
around these.
"""

import subprocess
import sys
from pathlib import Path

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
