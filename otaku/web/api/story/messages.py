"""A story's messages, as the page asks for them: the chain, one message
and its correction — and a line played, a write whose answer is the
reply's event stream (`STREAMS`), each event named on the wire by
`event`, as a regenerated reply's is (`stories`)."""

import base64
from collections.abc import Iterator, Mapping
from dataclasses import asdict
from typing import Any

from otaku.backend.api import Api
from otaku.backend.api.story.messages import (
    AssistantMessage,
    AssistantReasoning,
    AssistantText,
    AssistantToolCall,
    Declined,
    Done,
    Failed,
    RawFile,
    ToolMessage,
    UserMessage,
)
from otaku.backend.api.story.messages import PlayEvent as PlayEvent
from otaku.web.api._request import Ask, Route, Stream, changed, plain
from otaku.web.api.story.stories import expected_head, tagged


def _play(api: Api, ask: Ask) -> Iterator[PlayEvent]:
    """One line played on the story, with the pictures attached to it,
    from the head `If-Match` names. Validation is eager: a refusal, a
    head standing elsewhere and a malformed body reach the server before
    any of this is streamed — and before anything is recorded."""
    story_id = ask.id("story")
    files = _files_from(ask.body)
    return api.stories.messages.create(
        story_id,
        line=ask.text("line"),
        files=files,
        expected_head=expected_head(api, ask, story_id),
    )


def _files_from(body: Mapping[str, Any]) -> list[RawFile]:
    """The pictures a play body carries: `files`, each a name and its
    bytes as base64 — a body that is not what it claims is a malformed
    request (400) before the backend is asked for anything."""
    rows = body.get("files") or []
    if not isinstance(rows, list):
        raise TypeError("files is not a list")
    return [
        RawFile(base64.b64decode(str(row["data"]), validate=True), str(row.get("name", "")))
        for row in rows
    ]


def event(happened: PlayEvent) -> dict[str, Any]:
    """One play event as the page reads it. The union is closed and the
    match is exhaustive: a new event kind is a type error here, not a
    silence on the wire."""
    match happened:
        case UserMessage():
            # `note` is the record's own dim line (a /roll's dice); ""
            # rides along so the shape never depends on the message.
            return {
                "type": "user_message",
                "message": plain(happened.message),
                "note": happened.note,
            }
        case AssistantReasoning():
            return {"type": "assistant_reasoning", "text": happened.text}
        case AssistantText():
            # `join_type` on the reply's first piece alone, null after: how
            # the arriving reply joins the text before it, on the canvas.
            return {
                "type": "assistant_text",
                "text": happened.text,
                "join_type": plain(happened.join_type),
            }
        case AssistantToolCall():
            # A piece of a tool call, the tool's name and its inside;
            # `closed` marks the piece the closing fence ended. The tool's
            # label, who acts on it and what the status row says while it
            # is called ride along where the tool is known.
            return {
                "type": "assistant_tool_call",
                "tool": happened.name,
                "label": happened.label,
                "actor": plain(happened.actor),
                "status": happened.status,
                "text": happened.text,
                "closed": happened.closed,
            }
        case AssistantMessage():
            # The model's row as recorded — split, tags and all — so the
            # page lands it as the message it is, in place of what streamed.
            return {"type": "assistant_message", "message": plain(happened.message)}
        case ToolMessage():
            # The tool's row: the page draws it after the reply it
            # answers, then opens the block the reply that continues
            # streams into.
            return {"type": "tool_message", "message": plain(happened.message)}
        case Declined():
            return {"type": "declined", "reason": happened.reason}
        case Failed():
            return {"type": "failed", "reason": happened.reason}
        case Done():
            report = happened.report
            return {
                "type": "done",
                "stats": happened.stats,
                # The reply's report as facts, the derived ones included,
                # and its notice — a reply cut short says so.
                "report": asdict(report.stats)
                | {
                    "context_in_force": report.context_in_force,
                    "truncated": report.truncated,
                    "rate": report.rate,
                    "context_used": report.context_used,
                }
                if report is not None
                else None,
                "notice": report.notice if report is not None else "",
            }


def _list(api: Api, ask: Ask) -> dict[str, Any]:
    """The story's chain, root to head — its `last` messages alone where the
    query asks for a count."""
    raw = ask.query.get("last")
    if raw is not None and not (raw.isdigit() and int(raw) > 0):
        raise ValueError("last is not a count from 1")
    last = int(raw) if raw is not None else None
    return {"items": plain(api.stories.messages.list(ask.id("story"), last=last))}


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/stories/{story}/messages"): tagged(_list),
    ("GET", "/api/stories/{story}/messages/{message}"): tagged(
        lambda api, ask: plain(api.stories.messages.get(ask.id("story"), ask.id("message")))
    ),
    ("PATCH", "/api/stories/{story}/messages/{message}"): tagged(
        lambda api, ask: changed(
            api.stories.messages.update(ask.id("story"), ask.id("message"), body=ask.need("body"))
        )
    ),
}

# The write whose answer is the reply as it arrives — matched as the
# routes are, answered by the server's stream (`web.server.streams`).
STREAMS: dict[tuple[str, str], Stream] = {
    ("POST", "/api/stories/{story}/messages"): _play,
}
