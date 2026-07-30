"""The export side: a story out of the store, into the document."""

import json

from otaku.store import Store
from otaku.transfer import (
    EXPORT_FORMAT_VERSION,
    EXPORT_MARKER,
    ExportedCharacter,
    ExportedJournal,
    ExportedMessage,
    ExportedScene,
    StoryExport,
)
from otaku.transfer.imports import STRUCTURE_LINE


def read_story(store: Store, story_id: int) -> StoryExport:
    """The story out of the store as a `StoryExport`: its current chain
    and current scenes, ready for `render_story`."""
    story = store.stories.get(story_id)
    messages = store.stories.get_messages(story_id)
    ordinal = {m.id: i for i, m in enumerate(messages, 1)}
    scenes = store.scenes.get_current(story_id, list(ordinal))
    names = {c.id: c.name for c in store.characters.list(story_id)}
    by_scene: dict[int, list[ExportedJournal]] = {}
    current = {s.id for s in scenes}
    for journal in store.journals.list(story_id):
        if journal.scene_id in current:
            by_scene.setdefault(journal.scene_id, []).append(
                ExportedJournal(
                    character=names.get(journal.character_id, "?"),
                    entry=journal.entry,
                    state=journal.state,
                    history=journal.history,
                )
            )
    return StoryExport(
        title=story.title if story else "",
        system=story.system if story else "",
        story_so_far=store.scenes.get_story_so_far(story_id, list(ordinal)),
        cast=tuple(
            ExportedCharacter(c.name, c.aliases, c.description)
            for c in store.characters.list(story_id)
        ),
        scenes=tuple(
            ExportedScene(
                title=s.title,
                span=(ordinal[s.start_message_id], ordinal[s.end_message_id])
                if s.start_message_id in ordinal and s.end_message_id in ordinal
                else None,
                summary=s.summary,
                journals=tuple(by_scene.get(s.id, ())),
            )
            for s in scenes
        ),
        messages=tuple(
            ExportedMessage(
                role=m.role,
                body=m.body,
                kind=m.kind,
                speaker=m.speaker,
                framing=m.framing,
            )
            for m in messages
        ),
    )


def render_story(export: StoryExport, *, otaku_version: str, model: str, exported: str) -> str:
    """The export document for `export` — see the module docstring for the
    layout. `otaku_version`, `model`, and `exported` are provenance only;
    the parser reads none of them."""
    out: list[str] = []
    if export.title:
        out += [f"# {export.title}", ""]
    out += [
        EXPORT_MARKER,
        f"otaku-version: {otaku_version}",
        f"format-version: {EXPORT_FORMAT_VERSION}",
        f"model: {model}",
        f"exported: {exported}",
        "-->",
        "",
    ]

    if export.story_so_far or export.system or export.cast:
        out += ["## Story", ""]
        if export.story_so_far:
            out += ["### Story so far", "", _escape(export.story_so_far), ""]
        if export.system:
            out += ["### System", "", _escape(export.system), ""]
        if export.cast:
            out += ["### Cast", ""]
            for character in export.cast:
                aka = f" (aka {', '.join(character.aliases)})" if character.aliases else ""
                desc = f" — {character.description}" if character.description else ""
                out.append(f"- **{character.name}**{aka}{desc}")
            out.append("")

    if export.scenes:
        out += ["## Scenes", ""]
        for n, scene in enumerate(export.scenes, 1):
            out.append(f"### {n} · {scene.title}" if scene.title else f"### {n}")
            if scene.span is not None:
                first, last = scene.span
                span = str(first) if first == last else f"{first}-{last}"
                out.append(f"- **Messages:** {span}")
            out.append("")
            if scene.summary:
                out += [_escape(scene.summary), ""]
            for journal in scene.journals:
                out += [f"#### {journal.character}", ""]
                if journal.state:
                    out.append(f"**State:** {_escape(journal.state)}")
                if journal.history:
                    out.append(f"**History:** {_escape(journal.history)}")
                if journal.entry:
                    out.append(f"**Entry:** {_escape(journal.entry)}")
                out.append("")

    out += ["## Messages", ""]
    for n, message in enumerate(export.messages, 1):
        kind = f" ({message.kind})" if message.kind != "dialogue" else ""
        header = f"### {n} · {message.role}{kind}"
        if message.speaker:
            header += f" · {message.speaker}"
        if message.framing is not None:
            # JSON-quoted: newlines survive, and the quotes tell it apart
            # from a speaker name.
            header += f" · {json.dumps(message.framing, ensure_ascii=False)}"
        out += [header, _escape(message.body), ""]
    return "\n".join(out).rstrip() + "\n"


def _escape(text: str) -> str:
    """Free text, made structure-proof: a heading-shaped line gains one
    leading backslash; the parser's `_unescape` strips exactly one back."""
    return "\n".join(
        STRUCTURE_LINE.sub(lambda m: "\\" + m.group(0), line) for line in text.splitlines()
    )
