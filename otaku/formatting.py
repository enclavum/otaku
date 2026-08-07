"""Small text helpers for terminal output."""

from datetime import UTC, datetime
from pathlib import Path

# Control characters display must drop: C0 minus newline and tab, DEL, and
# C1 (U+0080..U+009F) — xterm honors 8-bit CSI/OSC aliases even in UTF-8 mode.
_CONTROL = {c: None for c in range(32) if chr(c) not in "\n\t"}
_CONTROL[0x7F] = None
_CONTROL.update(dict.fromkeys(range(0x80, 0xA0)))


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


def printable(text: str) -> str:
    """`text` with every control character a terminal could act on
    dropped — C0 except newline and tab, DEL, the C1 range, and with
    them any escape sequence's lead byte. Model output and server
    messages pass through
    here before display, so a hostile stream can never move the cursor,
    retitle the window, or touch the clipboard — and the screen ledger's
    row math stays true. Only what is shown is filtered; storage keeps
    every byte."""
    return text.translate(_CONTROL)


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
    """A context window the way the catalogs label it: '8K', '128K',
    '200K', '1M'. K and M are DECIMAL here — thousands and millions —
    because that is how every model catalog reads them, so a round
    decimal size wins its label: 128,000 is the 128K its vendor
    advertises, never the 125K that dividing by 1024 would give. A size
    that is instead an exact power of two takes the label ITS vendor
    prints (131,072 is 128K too), and anything else rounds to a whole
    one (1,047,576 → '1M'). Under a thousand the count stands as it is;
    "" when unknown.

    Sizes a few percent apart therefore share a label — 1,000,000,
    1,047,576 and 1,048,576 all read '1M'. That is the vocabulary the
    catalogs already use, the difference never decides a model, and it
    is what keeps the label short enough to hold a fixed column."""
    if tokens is None or tokens <= 0:
        return ""
    for unit, decimal, binary in (("M", 1_000_000, 1_048_576), ("K", 1_000, 1_024)):
        if tokens >= decimal and tokens % decimal == 0:
            return f"{tokens // decimal}{unit}"
        if tokens >= binary and tokens % binary == 0:
            return f"{tokens // binary}{unit}"
        if tokens >= decimal:
            return f"{round(tokens / decimal)}{unit}"
    return str(tokens)


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
