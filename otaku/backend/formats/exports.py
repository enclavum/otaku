"""The export side: a story out of the store, into the document."""

import json

from otaku.backend.formats import (
    EXPORT_FORMAT_VERSION,
    EXPORT_MARKER,
    ExportedCharacter,
    ExportedJournal,
    ExportedMessage,
    ExportedScene,
    ExportedStory,
)
from otaku.backend.formats.imports import STRUCTURE_LINE
from otaku.store import Store


def read_story(store: Store, story_id: int) -> ExportedStory:
    """The story out of the store as an `ExportedStory`: its current chain
    and current scenes, ready for `render_story`. Bodies export as
    stored — a card row keeps its typed `/card` line — and each cast
    row's card profile travels with the cast, so a re-import restores
    the dualism intact: the typed line on screen, the block composed
    from the profile on the wire."""
    story = store.stories.get(story_id)
    # A tool's turns stay out until the format decides their place.
    messages = [m for m in store.stories.get_messages(story_id) if m.role != "tool"]
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
    return ExportedStory(
        title=story.title if story else "",
        system=story.system if story else "",
        cast=tuple(
            ExportedCharacter(c.name, c.aliases, c.description, c.profile or "")
            for c in store.characters.list(story_id)
        ),
        scenes=tuple(
            ExportedScene(
                title=s.title,
                span=(ordinal[s.start_message_id], ordinal[s.end_message_id])
                if s.start_message_id in ordinal and s.end_message_id in ordinal
                else None,
                summary=s.summary,
                history=s.history,
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
                template=m.template,
                pinned=m.pinned,
            )
            for m in messages
        ),
    )


def render_story(export: ExportedStory, *, otaku_version: str, model: str, exported: str) -> str:
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

    # The story-so-far preface IS the newest scene's history — one fact,
    # written twice for the reader opening the file, carried once.
    arc = export.scenes[-1].history if export.scenes else ""
    if arc or export.system or export.cast:
        out += ["## Story", ""]
        if arc:
            out += ["### Story so far", "", _escape(arc), ""]
        if export.system:
            out += ["### System", "", _escape(export.system), ""]
        if export.cast:
            out += ["### Cast", ""]
            for character in export.cast:
                aka = f" (aka {', '.join(character.aliases)})" if character.aliases else ""
                desc = f" — {character.description}" if character.description else ""
                out.append(f"- **{character.name}**{aka}{desc}")
            out.append("")
            # A card profile under its carrier, escaped like any body —
            # card text is free to hold heading-shaped lines of its own.
            for character in export.cast:
                if character.profile:
                    out += [f"#### {character.name}", "", _escape(character.profile), ""]

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
            if scene.history:
                out += [f"**History:** {_escape(scene.history)}", ""]
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
        # the kind beside the role where it is not dialogue, and a pin
        marks = [message.kind] if message.kind != "dialogue" else []
        if message.pinned:
            marks.append("pinned")
        kind = f" ({', '.join(marks)})" if marks else ""
        header = f"### {n} · {message.role}{kind}"
        if message.speaker:
            header += f" · {message.speaker}"
        if message.template is not None:
            # JSON-quoted: newlines survive, and the quotes tell it apart
            # from a speaker name.
            header += f" · {json.dumps(message.template, ensure_ascii=False)}"
        out += [header, _escape(message.body), ""]
    return "\n".join(out).rstrip() + "\n"


def _escape(text: str) -> str:
    """Body text, made structure-proof: a heading-shaped line gains one
    leading backslash; the parser's `_unescape` strips exactly one back."""
    return "\n".join(
        STRUCTURE_LINE.sub(lambda m: "\\" + m.group(0), line) for line in text.splitlines()
    )
