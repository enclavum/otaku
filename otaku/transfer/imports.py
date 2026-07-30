"""The import side: the document (or prose) into the store.

`parse_story` reads the export document back into its parts;
`write_story` puts a `StoryExport` into the store as a new story — the
messages, and, when it carries scenes, the memory verbatim with no model
calls.
"""

import json
import re

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

# A message header: `### 3 · user (ooc) · Speaker · "framing"` — the
# speaker and the JSON-quoted framing optional, in that order.
_MSG_HEADER = re.compile(
    r"^(\d+)\s*·\s*(user|assistant)(?:\s*\((ooc|narration)\))?(?:\s*·\s*(.+))?$"
)
# `### 2 · The Crossing` / `### 2` — a scene header in `## Scenes`.
_SCENE_HEADER = re.compile(r"^(\d+)(?:\s*·\s*(.+))?$")
_CAST_BULLET = re.compile(r"^-\s*\*\*(.+?)\*\*(?:\s*\(aka\s*(.+?)\))?(?:\s*—\s*(.+))?$")
_SPAN_BULLET = re.compile(r"^-\s*\*\*Messages:\*\*\s*(\d+)(?:-(\d+))?$")
_FIELD = re.compile(r"^\*\*([A-Za-z ]+):\*\*\s?(.*)$")  # **State:** / **History:** / **Entry:**
# A free-text line that would parse as document structure — a heading of
# any level the format uses, or an already-escaped such line. The
# renderer's `_escape` adds one backslash; `_unescape` strips one back.
STRUCTURE_LINE = re.compile(r"^(\\*)(#{1,4} )")


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
            story_so_far = _unescape(_strip_edges(body))
        elif header == "System":
            system = _unescape(_strip_edges(body))
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
                entry=_unescape((fields := _labeled_fields(jbody)).get("entry", "")),
                state=_unescape(fields.get("state", "")),
                history=_unescape(fields.get("history", "")),
            )
            for name, jbody in journal_secs
        )
        scenes.append(
            ExportedScene(scene_title, span, _unescape(_strip_edges(summary_lines)), journals)
        )

    messages: list[ExportedMessage] = []
    _, msg_secs = _split_by_header(blocks.get("Messages", []), "### ")
    for header, mbody in msg_secs:
        m = _MSG_HEADER.match(header)
        if m is None:
            continue
        speaker, framing = _speaker_and_framing(m.group(4) or "")
        messages.append(
            ExportedMessage(
                role=m.group(2),
                body=_unescape(_strip_edges(mbody)),
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

    def character(name: str, aliases: tuple[str, ...] = (), description: str | None = None) -> int:
        # Resolve-or-create by name; an existing row is enriched, never
        # overwritten (the store's own additive rule).
        found = store.characters.find(story_id, name)
        if found is not None:
            if aliases or description:
                store.characters.update(found.id, aliases=aliases, description=description)
            return found.id
        return store.characters.add(story_id, name, aliases=aliases, description=description)

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
            store.messages.set_speaker(message_id, character(message.speaker), message.speaker)
        ids.append(message_id)

    for member in export.cast:
        character(member.name, aliases=member.aliases, description=member.description or None)

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
                character(journal.character),
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
    """Split lines at headers of exactly `marker` level (e.g. '## ').
    Returns (lines before the first header, [(header, body)]). The
    trailing space in `marker` excludes deeper levels — '## ' never
    matches '### '. Content cannot masquerade as a header: the renderer
    escapes structure-shaped free-text lines."""
    preamble: list[str] = []
    header: str | None = None
    body: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith(marker):
            if header is not None:
                sections.append((header, body))
            header, body = line[len(marker) :].strip(), []
            continue
        (body if header is not None else preamble).append(line)
    if header is not None:
        sections.append((header, body))
    return preamble, sections


def _speaker_and_framing(extra: str) -> tuple[str | None, str | None]:
    """The header's trailing fields: an optional bare speaker, then an
    optional JSON-quoted framing — the quote is what tells them apart. (A
    speaker name containing ` · ` is the one thing this cannot carry.)"""
    extra = extra.strip()
    if not extra:
        return None, None
    if extra.startswith('"'):
        return None, _json_string(extra)
    speaker, sep, quoted = extra.partition(' · "')
    if sep:
        return speaker.strip() or None, _json_string('"' + quoted)
    return extra, None


def _json_string(text: str) -> str | None:
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, str) else None


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


def _unescape(text: str) -> str:
    """The renderer's structure escape, undone: one leading backslash off
    every heading-shaped free-text line (see `exports._escape`)."""
    return "\n".join(
        STRUCTURE_LINE.sub(lambda m: m.group(1)[1:] + m.group(2), line)
        for line in text.splitlines()
    )
