"""The lore, as the page asks for it: the cast, a scene's, a character's
and a journal's writable fields, a merge, and the extraction pass the
page starts, stops and polls — the flows keyed by story in `Pending`."""

from typing import Any

from otaku.backend import (
    Journal,
)
from otaku.backend.api import lore as api_lore
from otaku.backend.api.lore import FieldKind
from otaku.backend.session import Refused, Session
from otaku.web.api.request import Ask, Flow, Pending, Route


def cast(session: Session) -> dict[str, Any]:
    """The open story's cast, for the composer's name menu — the cheap
    per-keystroke read `backend.api.lore.cast` exists for. Empty with no
    story or an empty cast: a menu question is never a refusal."""
    return {
        "characters": [
            {"id": row.id, "name": row.name, "description": row.description}
            for row in api_lore.cast(session)
        ]
    }


def _edit_scene(session: Session, ask: Ask) -> str:
    """A scene's own two fields. The arc (`history`) is derived and has
    no write — correcting the entries rebuilds it."""
    return _edit_lore(session, ask, "scene", {"title": "scene-title", "summary": "scene-summary"})


def _edit_character(session: Session, ask: Ask) -> str:
    return _edit_lore(session, ask, "character", {"description": "description", "card": "card"})


def _edit_journal(session: Session, ask: Ask) -> str:
    """A journal record's entry. The state is the extractor's own — like
    both histories it has no write; correcting the entry is what moves
    the record."""
    return _edit_lore(session, ask, "record", {"entry": "entry"})


def _edit_lore(session: Session, ask: Ask, target: str, kinds: dict[str, FieldKind]) -> str:
    """One corrected row of the memory. The PATH says which row — the
    story, then a scene, a character, or a journal record — and the body
    says which of its fields; `api_lore.edit` takes all three as one
    address and CHECKS the row is that story's.

    Fields are applied in order and each answers for itself: a body
    carrying two (the page sends one, the spec allows more) must not
    let a later refusal claim nothing was saved when an earlier field
    was — so a mixed outcome says both, and only an outcome with no
    save at all is a refusal."""
    said: list[str] = []
    refused: list[str] = []
    for name, kind in kinds.items():
        if name in ask.body:
            try:
                # `need`, not a bare str(): a null title would be stored
                # as the literal word "None" (`Ask.need`).
                said.append(
                    api_lore.edit(session, kind, ask.id(target), ask.need(name), ask.id("story"))
                )
            except Refused as refusal:
                refused.append(str(refusal))
    if not said and refused:
        raise Refused(" ".join(refused))
    if not said:
        raise Refused(f"Nothing to change — send one of {', '.join(kinds)}.")
    return " ".join(said + refused)


def _merge(session: Session, ask: Ask) -> str:
    return api_lore.merge_by_id(session, ask.id("character"), int(ask.body["into"]))


def _stop_extract(session: Session, ask: Ask, pending: Pending) -> dict[str, Any]:
    """Give up on the pass the page forced — the door Ctrl+C opens in
    the terminal, which is the only other way to leave one. Nothing
    half-done commits; already-closed scenes stay. Nothing running is an
    expected decline, refused like every other: a pass can finish
    between the reader asking and this arriving, and an automatic pass
    is the worker's own — the page never started it and cannot end it."""
    running = pending.extraction.get(ask.id("story"))
    if running is None or running.poll() is not None:
        raise Refused("No pass is running.")
    return {"notice": running.cancel()}


def _start_extract(session: Session, ask: Ask, pending: Pending) -> dict[str, Any]:
    """Force an extraction pass now and keep the run, so the page can
    ask for its report without ever holding the session's one thread. A
    second pass would overwrite the run the page is polling, and the
    first one's report would never be read."""
    story = ask.id("story")
    running = pending.extraction.get(story)
    if running is not None and running.poll() is None:
        return {"notice": "A pass is already running.", "watching": True}
    pending.extraction[story] = api_lore.extract(session)
    return {"notice": "Extracting lore from the recent messages…", "watching": True}


def memory(session: Session, story_id: int) -> dict[str, Any]:
    """A story's memory as the dossier draws it — how far the extractor
    has read and what is still open, its scenes with who was present
    and their journals, its characters with their arcs — the lore half
    of `stories.story`'s one read, from one view so a pass landing
    between two asks cannot hand the page a torn story."""
    view = api_lore.view(session, story_id)
    return {
        # How far the extractor has read, and what is still open — the
        # facts the index's pending row and the extract block draw.
        "read_through": view.read_through(),
        "unread": view.unread(),
        "unread_span": view.unread_span(),
        "scenes": [
            {
                "id": scene.id,
                "number": number,
                # From the view's own data: a scene whose span cannot be
                # computed drops it, which would make a title read as a
                # message range.
                "span": view.scene_span(scene.id),
                "title": scene.title,
                "summary": scene.summary,
                # The arc THROUGH this scene — derived, so no write takes
                # it — and when the extractor last wrote the scene (an
                # audit stamp, display alone).
                "history": scene.history,
                "updated_at": scene.updated_at,
                "present": [
                    character.name
                    for character in view.cast
                    if any(
                        record.scene_id == scene.id and record.character_id == character.id
                        for record in view.journals
                    )
                ],
                "journals": [
                    journal(record) for record in view.journals if record.scene_id == scene.id
                ],
            }
            for number, scene in enumerate(view.scenes, start=1)
        ],
        "characters": [
            {
                "id": character.id,
                "name": character.name,
                "aliases": list(character.aliases),
                "description": character.description,
                # The archive an IMPORTED character carries; "" for one
                # the extractor named out of the story itself.
                "card": character.card or "",
                # Their arc SO FAR — derived, so no write takes it, the
                # way a scene's `history` above is derived.
                "history": view.character_history(character.id),
                # When the extractor last wrote them — display alone.
                "updated_at": character.updated_at,
                "journals": [
                    journal(record)
                    for record in view.journals
                    if record.character_id == character.id
                ],
            }
            for character in view.cast
        ],
    }


def journal(record: Journal) -> dict[str, Any]:
    """One character's line in one scene — the thing BOTH lenses show,
    which is why it carries the two sides it hangs between. `id` is what
    a correction addresses; a scene draws these under its own heading, a
    character draws the same rows from the other side."""
    return {
        "id": record.id,
        "scene": record.scene_id,
        "character": record.character_id,
        "entry": record.entry,
        "state": record.state,
        # When the extractor last wrote it — an audit stamp, display alone.
        "updated_at": record.updated_at,
    }


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/cast"): lambda session, ask: cast(session),
    ("PATCH", "/api/stories/{story}/scenes/{scene}"): _edit_scene,
    ("PATCH", "/api/stories/{story}/characters/{character}"): _edit_character,
    ("PUT", "/api/stories/{story}/characters/{character}/merge"): _merge,
    ("PATCH", "/api/stories/{story}/journals/{record}"): _edit_journal,
}


FLOWS: dict[tuple[str, str], Flow] = {
    ("POST", "/api/stories/{story}/extraction"): _start_extract,
    ("DELETE", "/api/stories/{story}/extraction"): _stop_extract,
}
