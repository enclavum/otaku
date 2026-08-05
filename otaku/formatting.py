"""Small text helpers for terminal output."""

from datetime import UTC, datetime
from pathlib import Path


def combine_framing(body: str, framing: str | None) -> str:
    """The composed text of one turn: its body plus the framing a command
    wrote, stored in separate columns and joined only on demand so the body
    stays verbatim. No framing → the bare body. A `{body}` placeholder in
    the framing → the body slotted there (via `str.replace`, never
    `str.format`, so other braces stay literal). Otherwise framing, a blank
    line, then the body — or the framing alone when there is no body (a
    `/you` turn)."""
    if not framing:
        return body
    if "{body}" in framing:
        return framing.replace("{body}", body)
    if not body:
        return framing
    return f"{framing}\n\n{body}"


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


def human_age(t: datetime) -> str:
    """How long ago an aware timestamp was, the way a human says it: "just
    now" under a minute, then the largest fitting unit ("7m ago", "3h ago",
    "12d ago")."""
    sec = (datetime.now(UTC) - t.astimezone(UTC)).total_seconds()
    if sec < 60:
        return "just now"
    if sec < 3600:
        return f"{int(sec // 60)}m ago"
    if sec < 86400:
        return f"{int(sec // 3600)}h ago"
    return f"{int(sec // 86400)}d ago"
