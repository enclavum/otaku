"""Playing, as the page asks for it: the turns as stored (`turn`, the
one shape every stored turn crosses in), the reply's event stream and
each event's name on the wire, the pictures a turn names, the composer's
language and its history. The rows are `ROUTES`; the two that PLAY
answer with a stream and are the server's own."""

import base64
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from otaku.backend import (
    Message,
    commands,
)
from otaku.backend.api import play as api_play
from otaku.backend.api.play import (
    Declined,
    Done,
    Failed,
    PlayEvent,
    Reasoning,
    Recorded,
    Text,
    ToolCall,
)
from otaku.backend.commands import COMMANDS, PROSE_DESCRIPTION
from otaku.backend.files import RawFile
from otaku.backend.session import Refused, Session
from otaku.web.api.request import Ask, Blob, NotFound, Route


def _turns(session: Session) -> list[dict[str, Any]]:
    """The open story, one row per stored turn, oldest first."""
    return [turn(message) for message in session.messages]


def _history(session: Session) -> list[str]:
    """The composer's ↑/↓ history, most recent first — the same
    store-backed lines the terminal prompt walks, so a reload (or a
    session on the other frontend) starts with the history it left."""
    return session.history()


def syntax() -> dict[str, Any]:
    """The story's typed LANGUAGE — not its commands. The openers a line
    may start with and the inliners it may carry, which is what the
    composer's menu offers and the help sheet lists.

    Tokens and argument shapes only. What each one MEANS in a menu is
    the page's own caption: a sheet has room for a caption where the
    shared table's row is a sentence, and where a word is drawn is the
    medium's business. The rows themselves are declared once, in
    `context.syntax`, and reach here through the shared table."""
    rows = [spec for spec in COMMANDS if spec.kind is commands.CommandKind.SYNTAX]
    return {
        # What a line with no framing does — the one row that is not a
        # word, and the sentence the sheet opens with.
        "prose": PROSE_DESCRIPTION,
        "openers": [
            {"token": spec.token, "args": spec.args}
            for spec in rows
            if not spec.token.startswith("…")
        ],
        "inliners": [
            {"token": spec.token, "args": spec.args} for spec in rows if spec.token.startswith("…")
        ],
    }


#
# One entry in ROUTES each, and nothing else: the table at the foot of
# this module is the whole list, and a function here that is not in it
# would be a door nobody can open.


def _undo(session: Session) -> str:
    """Take back the trailing exchange — the popped rows are the page's
    cue to redraw, and the sentence says what happened. Nothing to take
    is a refusal like every other, so the page reads the `refused` flag
    the server marks them with and never the wording — which is COPIED:
    the terminal refuses with the same "Nothing to undo."
    (`chat.bindings`, its home), and the backend hands back only the
    rows."""
    popped = api_play.undo(session)
    if not popped:
        raise Refused("Nothing to undo.")
    return f"Took back the last exchange ({len(popped)} messages)."


def _record_history(session: Session, ask: Ask) -> str:
    """One submitted composer line into the ↑/↓ history — what the
    terminal prompt does at its own door, fired by the page beside every
    submission (blanks and immediate repeats are the session's to skip).
    Nothing to say back: the submission itself is the event."""
    session.record_history(ask.need("line"))
    return ""


def play(session: Session, line: str, files: Sequence[RawFile] = ()) -> Iterator[PlayEvent]:
    """One submitted story line, with the pictures attached to it.
    Validation is eager, so a Refused reaches the caller before any of
    this is streamed — and before anything is recorded."""
    return api_play.submit(session, line, files)


def files_from(body: Mapping[str, Any]) -> list[RawFile]:
    """The pictures a play body carries: `files`, each a name and its
    bytes as base64 — decoded here, on the handler's thread, so a body
    that is not what it claims is a malformed request (400) before the
    session's thread is asked for anything."""
    rows = body.get("files") or []
    if not isinstance(rows, list):
        raise TypeError("files is not a list")
    return [
        RawFile(base64.b64decode(str(row["data"]), validate=True), str(row.get("name", "")))
        for row in rows
    ]


def _picture(session: Session, name: str) -> Blob:
    found = api_play.picture(session, name)
    if found is None:
        raise NotFound
    data, media_type = found
    return Blob(data, media_type)


def _thumbnail(session: Session, name: str) -> Blob:
    data = api_play.thumbnail(session, name)
    if data is None:
        raise NotFound
    return Blob(data, "image/jpeg")


def regenerate(session: Session) -> Iterator[PlayEvent]:
    """Sibling the standing reply and stream the fresh take. Eager like
    `play`: without a model, or with nothing to regenerate, it refuses
    before anything is dropped."""
    return api_play.regenerate(session)


def event(happened: PlayEvent) -> dict[str, Any]:
    """One play event as the page reads it. The union is closed and the
    match is exhaustive: a new event kind is a type error here, not a
    silence on the wire."""
    match happened:
        case Recorded():
            # `note` is the record's own dim line (a /roll's dice); ""
            # rides along so the shape never depends on the turn.
            return {"type": "recorded", "turn": turn(happened.message), "note": happened.note}
        case Reasoning():
            return {"type": "reasoning", "text": happened.text}
        case Text():
            return {"type": "text", "text": happened.text}
        case ToolCall():
            # A piece of a tool call, the tool's name and its inside;
            # `closed` marks the piece the closing fence ended.
            return {
                "type": "tool_call",
                "tool": happened.name,
                "text": happened.text,
                "closed": happened.closed,
            }
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
                    "max_context": report.max_context,
                    "truncated": report.truncated,
                    "rate": report.rate,
                    "context_used": report.context_used,
                }
                if report is not None
                else None,
                "notice": report.notice if report is not None else "",
                # The reply as stored — split, tags and all — so the page lands
                # it as the turn it is; null when nothing arrived.
                "reply": turn(happened.reply) if happened.reply else None,
            }


def turn(message: Message) -> dict[str, Any]:
    """One stored turn as the page reads it: the body, and the facts
    recorded with it — the kind the language stored it as, who answered
    an assistant turn and with what template, and the speaker the
    extractor named (None until its pass reads the turn, which is a
    state the reader pane draws as pending)."""
    return {
        "id": message.id,
        "role": message.role,
        "body": message.body,
        "kind": message.kind,
        "speaker": message.speaker,
        "provider": message.provider,
        "model": message.model,
        "template": message.template,
        # The turn's pictures, the column's own facts: the name the
        # file routes take, the media type, the measure. [] when none.
        "attachments": [asdict(picture) for picture in message.attachments],
        # The body split: prose, and each block as its tool's facts — the
        # page draws from these and parses nothing. A block of a tag no
        # tool owns is a block all the same, with its text alone.
        "segments": api_play.segments(message.body),
    }


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/play"): lambda session, ask: {"messages": _turns(session)},
    ("DELETE", "/api/play/last"): lambda session, ask: _undo(session),
    ("GET", "/api/play/syntax"): lambda session, ask: syntax(),
    # A turn's pictures, by the name its row names them under.
    ("GET", "/api/files/{file}"): lambda session, ask: _picture(session, ask.params["file"]),
    ("GET", "/api/files/{file}/thumb"): lambda session, ask: _thumbnail(
        session, ask.params["file"]
    ),
    ("GET", "/api/history"): lambda session, ask: {"lines": _history(session)},
    ("POST", "/api/history"): _record_history,
}
