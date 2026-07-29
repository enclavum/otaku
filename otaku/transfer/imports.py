"""The import side: the document (or prose) into the store.

`parse_story` reads the export document back into its parts;
`write_story` puts a `StoryExport` into the store as a new story — the
messages, and, when it carries scenes, the memory verbatim with no model
calls.
"""

import json
import re

from otaku.lore.extraction import Cast
from otaku.store import Store
from otaku.store.schema import Message
from otaku.transfer import (
    EXPORT_MARKER,
    ExportedCharacter,
    ExportedJournal,
    ExportedMessage,
    ExportedScene,
    StoryExport,
)

_FENCE = re.compile(r"^(```|~~~)")
# `### 3 · user (ooc)` — a message header in the `## Messages` section.
_MSG_HEADER = re.compile(r"^(\d+)\s*·\s*(user|assistant)(?:\s*\((ooc|narration)\))?$")
# `### 2 · The Crossing` / `### 2` — a scene header in `## Scenes`.
_SCENE_HEADER = re.compile(r"^(\d+)(?:\s*·\s*(.+))?$")
_CAST_BULLET = re.compile(r"^-\s*\*\*(.+?)\*\*(?:\s*\(aka\s*(.+?)\))?(?:\s*—\s*(.+))?$")
_SPAN_BULLET = re.compile(r"^-\s*\*\*Messages:\*\*\s*(\d+)(?:-(\d+))?$")
_MSG_BULLET = re.compile(r"^-\s*\*\*(Speaker|Framing):\*\*\s?(.*)$")
_FIELD = re.compile(r"^\*\*([A-Za-z ]+):\*\*\s?(.*)$")  # **State:** / **History:** / **Entry:**


def parse_story(text: str) -> StoryExport | None:
    """The document back into its parts, or None when `text` isn't one
    (no export marker)."""
    if EXPORT_MARKER not in text:
        return None
    lines = text.splitlines()
    preamble, top = _split_by_header(lines, "## ")
    blocks = dict(top)

    title = ""
    for line in preamble:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    system = ""
    story_so_far = ""
    cast: list[ExportedCharacter] = []
    _, story_subs = _split_by_header(blocks.get("Story", []), "### ")
    for header, body in story_subs:
        if header == "Story so far":
            story_so_far = _strip_edges(body)
        elif header == "System":
            system = _strip_edges(body)
        elif header == "Cast":
            for line in body:
                if (m := _CAST_BULLET.match(line.strip())) is not None:
                    aliases = tuple(a.strip() for a in (m.group(2) or "").split(",") if a.strip())
                    cast.append(
                        ExportedCharacter(m.group(1).strip(), aliases, (m.group(3) or "").strip())
                    )

    scenes: list[ExportedScene] = []
    _, scene_secs = _split_by_header(blocks.get("Scenes", []), "### ")
    for header, sbody in scene_secs:
        m = _SCENE_HEADER.match(header)
        scene_title = (m.group(2) or "").strip() if m else header
        head, journal_secs = _split_by_header(sbody, "#### ")
        span: tuple[int, int] | None = None
        summary_lines: list[str] = []
        for line in head:
            if (sm := _SPAN_BULLET.match(line.strip())) is not None:
                first = int(sm.group(1))
                span = (first, int(sm.group(2)) if sm.group(2) else first)
            else:
                summary_lines.append(line)
        journals = tuple(
            ExportedJournal(
                character=name.strip(),
                entry=(fields := _labeled_fields(jbody)).get("entry", ""),
                state=fields.get("state", ""),
                history=fields.get("history", ""),
            )
            for name, jbody in journal_secs
        )
        scenes.append(ExportedScene(scene_title, span, _strip_edges(summary_lines), journals))

    messages: list[ExportedMessage] = []
    _, msg_secs = _split_by_header(blocks.get("Messages", []), "### ")
    for header, mbody in msg_secs:
        m = _MSG_HEADER.match(header)
        if m is None:
            continue
        speaker: str | None = None
        framing: str | None = None
        body_lines: list[str] = []
        for line in mbody:
            if (bm := _MSG_BULLET.match(line.strip())) is not None and not body_lines:
                if bm.group(1) == "Speaker":
                    speaker = bm.group(2).strip() or None
                else:
                    try:
                        decoded = json.loads(bm.group(2))
                    except json.JSONDecodeError:
                        decoded = None
                    framing = decoded if isinstance(decoded, str) else None
            else:
                body_lines.append(line)
        messages.append(
            ExportedMessage(
                role=m.group(2),
                body=_strip_edges(body_lines),
                kind=m.group(3) or "dialogue",
                speaker=speaker,
                framing=framing,
            )
        )

    return StoryExport(
        title=title,
        system=system,
        story_so_far=story_so_far,
        cast=tuple(cast),
        scenes=tuple(scenes),
        messages=tuple(messages),
    )


def write_story(store: Store, export: StoryExport) -> int:
    """Write an export into the store as a new story: the messages, and —
    when it carries scenes — the memory verbatim (cast, summaries,
    journals, histories), with no model calls. The title is applied only
    when the export names one; an import never invents a name. Whatever
    memory is missing, the next extraction pass builds. Returns the new
    story id."""
    story_id = store.stories.add(export.title or None)
    if export.system:
        store.stories.set_system(story_id, export.system)
    cast = Cast.load(store, story_id)

    ids: list[int] = []
    for message in export.messages:
        message_id = store.stories.append(
            story_id,
            Message(
                role=message.role,
                body=message.body,
                kind=message.kind,
                framing=message.framing,
            ),
        )
        if message.speaker:
            store.messages.set_speaker(
                message_id, cast.get_or_add(message.speaker), message.speaker
            )
        ids.append(message_id)

    for character in export.cast:
        cast.get_or_add(
            character.name,
            aliases=character.aliases,
            description=character.description or None,
        )

    newest_with_history = len(export.scenes) - 1 if export.story_so_far else None
    for i, scene in enumerate(export.scenes):
        if scene.span is None or not (1 <= scene.span[0] <= scene.span[1] <= len(ids)):
            continue
        scene_id = store.scenes.add(
            story_id,
            start_message_id=ids[scene.span[0] - 1],
            end_message_id=ids[scene.span[1] - 1],
            title=scene.title or None,
            summary=scene.summary or None,
            # The story so far rides the newest scene — all it ever is.
            history=export.story_so_far if i == newest_with_history else None,
        )
        for journal in scene.journals:
            journal_id = store.journals.add(
                story_id,
                scene_id,
                cast.get_or_add(journal.character),
                entry=journal.entry,
                state=journal.state,
            )
            if journal.history:
                store.journals.set_history(journal_id, journal.history)
    return story_id


# ---------- format internals ----------


def _split_by_header(
    lines: list[str], marker: str
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split lines at headers of exactly `marker` level (e.g. '## '),
    fence-aware. Returns (lines before the first header, [(header, body)]).
    The trailing space in `marker` excludes deeper levels — '## ' never
    matches '### '."""
    preamble: list[str] = []
    header: str | None = None
    body: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    in_fence = False
    for line in lines:
        if _FENCE.match(line.strip()):
            in_fence = not in_fence
        elif not in_fence and line.startswith(marker):
            if header is not None:
                sections.append((header, body))
            header, body = line[len(marker) :].strip(), []
            continue
        (body if header is not None else preamble).append(line)
    if header is not None:
        sections.append((header, body))
    return preamble, sections


def _strip_edges(lines: list[str]) -> str:
    out = list(lines)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _labeled_fields(lines: list[str]) -> dict[str, str]:
    """`**State:** …` / `**History:** …` / `**Entry:** …` blocks → a
    lowercased key→prose dict; a field's value runs until the next label."""
    fields: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    for line in lines:
        m = _FIELD.match(line)
        if m:
            if key is not None:
                fields[key] = "\n".join(buf).strip()
            key, buf = m.group(1).strip().lower(), [m.group(2)]
        elif key is not None:
            buf.append(line)
    if key is not None:
        fields[key] = "\n".join(buf).strip()
    return fields
