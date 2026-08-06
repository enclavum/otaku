"""Rendering the day-rotated logs for `otaku logs`: day-name handling,
the `--list` rows, and the request log's entry layout. The cli commands
only page and echo what this module shapes."""

import json
import re
from collections.abc import Iterator

from otaku.formatting import printable
from otaku.logs.requests import RequestLog


def resolve_day(day: str) -> str | None:
    """A DAY argument as the logs name their files (YYYYMMDD); the
    dashed form is accepted too. None when it is neither."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return day.replace("-", "")
    if re.fullmatch(r"\d{8}", day):
        return day
    return None


def dashed(stamp: str) -> str:
    """YYYYMMDD as YYYY-MM-DD — the human way the listings print."""
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"


def day_rows(days: list[tuple[str, int]]) -> list[str]:
    """The `--list` rows: one dashed day and its size per line."""
    return [f"{dashed(name)}  {size:>10,} B" for name, size in days]


def render_requests(log: RequestLog, stamp: str) -> Iterator[str]:
    """One day's request log as pager text: per entry a header row, the
    request's non-message fields as one JSON row, then each message."""
    for entry in log.read(stamp):
        yield f"=== {entry.ts}  {entry.provider}  [{entry.purpose}]\n"
        if entry.body is None:
            yield "  <unreadable: wrong key or corrupted>\n\n"
            continue
        meta = {k: v for k, v in entry.body.items() if k != "messages"}
        # The log stores every byte as sent; the pager view filters, like
        # every display path (click echoes plainly when PAGER is cat).
        yield f"  {printable(json.dumps(meta, ensure_ascii=False))}\n"
        messages = entry.body.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    yield f"  [{message.get('role')}] {printable(str(message.get('content')))}\n"
        yield "\n"
