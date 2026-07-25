"""Small text helpers for terminal output."""

from pathlib import Path


def pretty_path(path: Path) -> str:
    """A path with the home dir shortened to `~`."""
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return "~" if relative == Path() else f"~/{relative}"


def flatten(text: str) -> str:
    """Prose as one line: every run of whitespace becomes a single space,
    the edges stripped — for previews that must fit a row."""
    return " ".join(text.split())


def truncate(text: str, limit: int) -> str:
    """At most `limit` display chars, ending with an ellipsis when cut."""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def format_size(size: int | None) -> str:
    """Bytes → human-readable, always one decimal in GB; em dash when
    unknown."""
    if size is None or size <= 0:
        return "—"
    return f"{size / 1024**3:.1f} GB"


def format_context(tokens: int | None) -> str:
    """A token count as a compact label ('8K', '128K', '1M') for exact
    multiples of 1024, thousands-separated otherwise; "" when unknown."""
    if tokens is None or tokens <= 0:
        return ""
    if tokens >= 1_048_576 and tokens % 1_048_576 == 0:
        return f"{tokens // 1_048_576}M"
    if tokens >= 1024 and tokens % 1024 == 0:
        return f"{tokens // 1024}K"
    return f"{tokens:,}"
