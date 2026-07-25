"""Small text helpers for terminal output."""

from pathlib import Path


def pretty_path(path: Path) -> str:
    """A path with the home dir shortened to `~`."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)
