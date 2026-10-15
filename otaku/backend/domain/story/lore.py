"""A story's memory, operated: loaded whole — its current scenes, the cast
in appearance order, the current timeline's journal records — a scene, a
character and a journal record each read and corrected, a character
linked to a codex card or unlinked, merged or deleted; and the forced
extraction pass, started through the worker, kept per story while it
runs and after (a run is read without the app's thread), and given up."""

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from otaku.backend.domain.app import App
from otaku.backend.domain.app._runs import WorkerRun
from otaku.backend.domain.codex import cards as codex_cards
from otaku.backend.domain.story import _request
from otaku.backend.errors import NO_MODEL_HINT, NO_SUCH_STORY, Conflict, Invalid, NotFound
from otaku.context.cards import CharacterProfile
from otaku.store.schema import Attachment, Card, Character, Journal, Scene

# A row of a story's memory, as `_theirs` finds it.
R = TypeVar("R", Scene, Character, Journal)


@dataclass(frozen=True)
class ExtractionState:
    """A forced pass as it stands: its `status` — "running", "done",
    "cancelled" or "failed" — and, once it is not running, its one-line
    `report`."""

    status: str
    report: str | None


@dataclass(frozen=True)
class Memory:
    """A story's memory loaded whole: how many messages its chain holds,
    its current scenes, the cast in appearance order, the current
    timeline's journal records, each chain message's ordinal by id, each
    codex card the cast is linked to, by its id, and — for each character
    a card row on the chain brought in, by its id — whether that row
    carries its card's picture."""

    story_id: int
    total_messages: int
    scenes: tuple[Scene, ...]
    cast: tuple[Character, ...]
    journals: tuple[Journal, ...]
    ordinal: dict[int, int]
    linked_cards: dict[int, Card]
    portraits_sent: dict[int, bool]

    def scene_no(self, scene_id: int) -> int:
        """1-based position of a scene in story order — the number every
        label leads with; 0 for a scene not in the memory."""
        return next((i + 1 for i, s in enumerate(self.scenes) if s.id == scene_id), 0)

    def scene_span(self, scene_id: int) -> str:
        """The scene's "N-M" message range on the CURRENT chain — "" when
        either end is off it (an edit moved the head past the scene)."""
        scene = next((s for s in self.scenes if s.id == scene_id), None)
        if scene is None:
            return ""
        start = self.ordinal.get(scene.start_message_id)
        end = self.ordinal.get(scene.end_message_id)
        if start is None or end is None:
            return ""
        return f"{start}-{end}"

    def messages_since(self, scene_id: int) -> int:
        """How far the story has moved past a scene's end, in messages."""
        scene = next((s for s in self.scenes if s.id == scene_id), None)
        if scene is None:
            return 0
        return self.total_messages - self.ordinal.get(scene.end_message_id, self.total_messages)

    def extracted_up_to(self) -> int:
        """The last message a closed scene covers, as a 1-based chain
        position — 0 when nothing has been read yet. Derived, never
        stored: a scene closes at a message, so the newest scene's end
        IS how far the extractor has read. What lies past it is no
        fault: the settle margin leaves the newest messages open on
        purpose, and a forced pass is what closes them early."""
        ends = [self.ordinal.get(s.end_message_id, 0) for s in self.scenes]
        return max(ends, default=0)

    def scene(self, scene_id: int) -> Scene:
        """One scene of the memory. Raises NotFound for one that is not in
        it — another story's, or a rewound timeline's."""
        return _theirs(self.scenes, scene_id, "scene")

    def character(self, character_id: int) -> Character:
        """One character of the cast. Raises NotFound as `scene`."""
        return _theirs(self.cast, character_id, "character")

    def journal(self, journal_id: int) -> Journal:
        """One journal record of the memory. Raises NotFound as `scene`."""
        return _theirs(self.journals, journal_id, "journal")

    def character_history(self, character_id: int) -> tuple[str, Journal | None]:
        """The character's arc SO FAR — their newest non-empty rollup, and
        the journal record it stands in; "" and None before a pass has
        written one. A scene's arc is a column of its own
        (`Scene.history`); a character's is not, so this is where it is
        read."""
        position = {s.id: i for i, s in enumerate(self.scenes)}
        rows = sorted(
            (j for j in self.journals if j.character_id == character_id),
            key=lambda r: position.get(r.scene_id, 0),
        )
        for row in reversed(rows):
            if row.history:
                return row.history, row
        return "", None


# ---------- the forced pass ----------


def create_extraction(app: App, story_id: int) -> tuple[ExtractionState, str]:
    """Run the extraction over a story right now, through the worker (one
    path into a pass, so a forced close can never race an automatic one):
    gate and settle margin dropped, closing right up to the last message
    — the pass as it stands, and the sentence. A pass already running for
    the story is the one answered: a second would set the first aside,
    and whoever waits on it would never read its report. Raises NotFound
    for a story that is not there, Conflict without a model to extract
    with."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    running = app._extraction_runs.get(story_id)
    if running is not None and running.poll() is None:
        return _state_of(running), "A pass is already running."
    if app._client(app._extraction_model()) is None:
        raise Conflict(NO_MODEL_HINT)
    run = WorkerRun(app._worker)
    job = replace(_request.build_job(app, story_id, force=True), on_done=run._finish)
    app._worker.schedule(job, now=True)
    app._extraction_runs[story_id] = run
    return _state_of(run), "Extracting lore from the recent messages\u2026"


def get_extraction(app: App, story_id: int) -> ExtractionState:
    """A story's forced pass as it stands — the one running, or the last
    one asked for. Safe from any thread: a run is read without the app's.
    Raises NotFound where none was asked for."""
    run = app._extraction_runs.get(story_id)
    if run is None:
        raise NotFound("No pass was asked for this story.")
    return _state_of(run)


def delete_extraction(app: App, story_id: int) -> str:
    """Give up a story's forced pass — nothing half-done commits;
    already-closed scenes stay — and the sentence that says so. Refused
    where none is running: a pass can finish between the asking and
    this, and an automatic pass is the worker's own, which nobody asked
    for and nobody cancels."""
    run = app._extraction_runs.get(story_id)
    if run is None or run.poll() is not None:
        raise Conflict("No pass is running.")
    return run.cancel()


def _state_of(run: WorkerRun) -> ExtractionState:
    """A pass as it stands: running, or done with its report — scenes
    closed, journals written, too little new play yet — cancelled, or
    failed."""
    report = run.poll()
    status = (
        "running"
        if report is None
        else "cancelled"
        if run.cancelled
        else "failed"
        if run.failed
        else "done"
    )
    return ExtractionState(status, report)


# ---------- the memory ----------


def get_lore(app: App, story_id: int) -> Memory:
    """A story's memory loaded whole. Raises NotFound for a story that
    is not there."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    store = app._store
    ids = store.stories.get_messages_ids(story_id)
    scenes = tuple(store.scenes.get_current(story_id, ids))
    current = {s.id for s in scenes}
    # In order of appearance (row order), not by name: the cast reads as
    # the story introduced it.
    cast = tuple(store.characters.list(story_id))
    linked = {c.card_id for c in cast if c.card_id is not None}
    chain = set(ids)
    sent = {
        speaker: bool(pictures)
        for row_id, speaker, pictures in store.messages.get_card_rows(story_id)
        if row_id in chain and speaker is not None
    }
    return Memory(
        story_id=story_id,
        total_messages=len(ids),
        scenes=scenes,
        cast=cast,
        journals=tuple(j for j in store.journals.list(story_id) if j.scene_id in current),
        ordinal={mid: i + 1 for i, mid in enumerate(ids)},
        linked_cards={card_id: codex_cards.get_card(app, card_id) for card_id in linked},
        portraits_sent=sent,
    )


def get_scene(app: App, story_id: int, scene_id: int) -> Scene:
    """One scene of a story's memory. Raises NotFound for one that is
    not in it — another story's, or a rewound timeline's."""
    return get_lore(app, story_id).scene(scene_id)


def get_character(app: App, story_id: int, character_id: int) -> Character:
    """One character of a story's cast. Raises NotFound as `scene`."""
    return get_lore(app, story_id).character(character_id)


def get_journal(app: App, story_id: int, journal_id: int) -> Journal:
    """One journal record of a story's memory. Raises NotFound as
    `scene`."""
    return get_lore(app, story_id).journal(journal_id)


def list_characters(app: App, story_id: int, *, name: str | None = None) -> tuple[Character, ...]:
    """A story's characters in appearance order — the cheap read the
    prompt menus and the pickers fire per keystroke — or, by `name`, the
    one a typed name or alias resolves to, case-insensitively (none for
    none). Empty for a story that is not there: a menu question is never
    a refusal."""
    if name is None:
        return tuple(app._store.characters.list(story_id))
    found = app._store.characters.find(story_id, name)
    return (found,) if found is not None else ()


def update_scene(
    app: App, story_id: int, scene_id: int, *, title: str | None = None, summary: str | None = None
) -> str:
    """The author's correction of a scene of a story's memory — None
    leaves a field as it is. The scene is CHECKED to be that story's, the
    way a message edit checks its chain: a browser addresses any story,
    and a correction that landed on another story's row would rewrite
    memory nobody was looking at. Its arc (`history`) is derived and has
    no write. Raises Refused for nothing to change, an emptied summary,
    and a scene not in the story's memory — nothing written."""
    get_scene(app, story_id, scene_id)
    if title is None and summary is None:
        raise Invalid("Nothing to change.")
    if summary is not None and not summary.strip():
        # A cleared summary would silently swallow the scene's span: the
        # assembler covers a scene only while its summary exists, and a
        # later scene's coverage still moves the tail past this one.
        # Extraction refuses to commit exactly this state.
        raise Invalid("An emptied summary would swallow its scene — not saved.")
    app._store.scenes.update(scene_id, title=title, summary=summary)
    return "Saved."


def update_character(
    app: App,
    story_id: int,
    character_id: int,
    *,
    description: str | None = None,
    profile: str | None = None,
) -> str:
    """The author's correction of a story's character — its description,
    and the profile an imported one carries, whole as TOML (emptied to
    nothing included). Checked to be that story's as `update_scene`
    checks. Raises Refused for nothing to change, a profile that is no
    TOML, and a character not in the cast — nothing written."""
    get_character(app, story_id, character_id)
    if description is None and profile is None:
        raise Invalid("Nothing to change.")
    if profile is not None:
        try:
            tomllib.loads(profile)
        except tomllib.TOMLDecodeError as e:
            # The profile is data other features parse — a broken edit is
            # refused the way an emptied summary is.
            raise Invalid(f"Not valid TOML — not saved: {e}") from e
    if description is not None:
        app._store.characters.set_description(character_id, description)
    if profile is not None:
        app._store.characters.set_profile(character_id, profile)
    return "Saved."


def profile_of(character: Character) -> CharacterProfile | None:
    """A story character's profile — its copy of its card's — read back
    field by field: every field, an empty one empty, under the name it
    came in as; None for a character no card brought, or one whose
    profile is no TOML."""
    if not character.profile:
        return None
    try:
        data = tomllib.loads(character.profile)
    except tomllib.TOMLDecodeError:
        return None
    return CharacterProfile.from_toml(character.profile, name=str(data.get("name", character.name)))


def update_profile(app: App, story_id: int, character_id: int, changes: Mapping[str, Any]) -> str:
    """The author's correction of a story character's profile, field by
    field (`codex.cards.FIELDS`), written back with the name it binds
    `{{user}}` to kept — a field emptied leaves the TOML, which keeps
    what a card holds. Raises Refused for nothing to change, a field a
    card has not, a character no card brought or a profile that is no
    TOML, a reminder's depth below 1, and a character not in the cast —
    nothing written."""
    character = get_character(app, story_id, character_id)
    if not changes:
        raise Invalid("Nothing to change.")
    unknown = sorted(set(changes) - set(codex_cards.FIELDS))
    if unknown:
        raise Invalid(f"A card has no {', '.join(unknown)}.")
    profile = profile_of(character)
    if profile is None:
        raise Invalid(f"{character.name} has no card to correct.")
    user = str(tomllib.loads(character.profile or "").get("user", ""))
    corrected = codex_cards.checked_profile(replace(profile, **changes))
    app._store.characters.set_profile(character_id, corrected.to_toml(user=user))
    return "Saved."


def set_portrait_sent(app: App, story_id: int, character_id: int, sent: bool) -> str:
    """Whether a character's picture rides the card row that brought it
    in, on the story's current timeline, to the model: its linked card's
    portrait, or none. Raises Refused for a character no card row there
    brought in, a picture asked for with no card linked or one without a
    picture, and a character not in the cast — nothing written."""
    character = get_character(app, story_id, character_id)
    chain = set(app._store.stories.get_messages_ids(story_id))
    rows = [
        row_id
        for row_id, speaker, _ in app._store.messages.get_card_rows(story_id)
        if speaker == character_id and row_id in chain
    ]
    if not rows:
        raise Invalid(f"{character.name} came into this story with no card.")
    pictures: tuple[Attachment, ...] = ()
    if sent:
        card = (
            codex_cards.get_card(app, character.card_id) if character.card_id is not None else None
        )
        if card is None or not card.pictures:
            raise Invalid(f"{character.name}'s card has no picture to send.")
        pictures = card.pictures[:1]
    for row_id in rows:
        app._store.messages.set_attachments(row_id, pictures)
    return "Saved."


def set_card_id(
    app: App, story_id: int, character_id: int, card_id: int | None, *, with_copies: bool = False
) -> str:
    """A story's character linked to a codex card — with `with_copies`, its
    copies too (`codex_cards.find_copies`) — or unlinked with None, alone;
    nothing else on either changes: the character keeps its own copy of
    whatever it holds. Checked to be that story's as `update_scene`
    checks; raises Refused for no such card, nothing to change, and a
    card another of the story's characters is linked to already."""
    if card_id is not None:
        character, card = _linkable(app, story_id, character_id, card_id)
        found = (
            codex_cards.find_copies(app, story_id, character, card_id=card.id)
            if with_copies
            else ()
        )
        app._store.characters.set_card_id(character.id, card.id, copies=[c for _, c in found])
        return f"Linked {character.name} to {card.name}." + codex_cards.linked_copies(len(found))
    character = get_character(app, story_id, character_id)
    if character.card_id is None:
        raise Invalid("Nothing to change.")
    was = codex_cards.get_card(app, character.card_id)
    app._store.characters.set_card_id(character.id, None)
    return f"Unlinked {character.name} from {was.name}."


def check_card_link(
    app: App, story_id: int, character_id: int, card_id: int
) -> tuple[tuple[int, int], ...]:
    """A link vetted before it is made, refused as `set_card_id` would
    be: the copies it may take along."""
    character, card = _linkable(app, story_id, character_id, card_id)
    return codex_cards.find_copies(app, story_id, character, card_id=card.id)


def _linkable(app: App, story_id: int, character_id: int, card_id: int) -> tuple[Character, Card]:
    """The character and the card a link joins, refused as `set_card_id`
    says."""
    character = get_character(app, story_id, character_id)
    if card_id == character.card_id:
        raise Invalid("Nothing to change.")
    card = codex_cards.get_card(app, card_id)
    other = next((c for c in app._store.characters.list(story_id) if c.card_id == card.id), None)
    if other is not None:
        raise Conflict(f"{other.name} in this story is linked to {card.name} already.")
    return character, card


def update_journal(app: App, story_id: int, journal_id: int, *, entry: str | None = None) -> str:
    """The author's correction of a journal record's entry. The state is
    the extractor's own and has no write: correcting the entry is what
    moves the record. Checked to be that story's as `update_scene`
    checks; raises Refused for nothing to change."""
    get_journal(app, story_id, journal_id)
    if entry is None:
        raise Invalid("Nothing to change.")
    app._store.journals.set_entry(journal_id, entry)
    return "Saved."


def merge_characters(app: App, story_id: int, character_id: int, *, into: int) -> str:
    """Fold a story's character into another — speakers and journals
    follow, the one folded becomes an alias of `into`, the character
    and the sentence. Refused toward the direction that would destroy an
    imported card's profile."""
    _ensure_cast_idle(app, story_id)
    # Resolved against THAT story's cast, so an id from another story is
    # as refused as an id that is not there at all.
    cast = {row.id: row for row in app._store.characters.list(story_id)}
    source, target = cast.get(character_id), cast.get(into)
    if source is None or target is None:
        raise NotFound("No such character in this story (see /cast).")
    if source.id == target.id:
        raise Invalid(f"'{source.name}' and '{target.name}' are already the same character.")
    if source.profile:
        # A merge deletes the source row, its card's profile with it — and
        # the card rows pointing at the target would send their typed
        # line instead of the composed block. Refused toward the
        # direction that keeps the profile.
        raise Conflict(
            f"{source.name} carries an imported card, which a merge would destroy — "
            f"merge the other direction (/merge {target.name} into {source.name}) "
            f"so the card survives."
        )
    app._store.characters.merge(story_id, source.id, target.id)
    # SOURCE is now an alias of TARGET, so a later /me or /you naming it
    # still resolves — nothing else to update.
    return f"Merged {source.name} into {target.name} ('{source.name}' is now an alias)."


def delete_character(app: App, story_id: int, character_id: int) -> str:
    """Remove a character the extractor should never have made, their
    journals with them, and their name from every line they spoke.
    Prose that mentions them (summaries, other characters' entries)
    stays, and a later pass may make them again from new messages.
    Refused for a character carrying an imported card: the greeting
    composes its block from that profile."""
    _ensure_cast_idle(app, story_id)
    character = _theirs(app._store.characters.list(story_id), character_id, "character")
    if character.profile:
        raise Conflict(
            f"{character.name} carries an imported card — "
            f"only an extracted character can be deleted."
        )
    app._store.characters.delete(character.id)
    return f"Deleted {character.name} and their journals."


def _ensure_cast_idle(app: App, story_id: int) -> None:
    """A pass resolves names against the cast it read at its start and
    writes each row alone, so a row removed under it would fail a scene
    half-written: the cast is not rewritten while one runs."""
    if app._worker.extracting_story_id == story_id:
        raise Conflict("An extraction pass is running — try again when it lands.")


def _theirs(rows: Iterable[R], target: int, noun: str) -> R:
    """The addressed row, which must be in the story's own memory — the
    same rule `stories.update_message` holds over its chain. The rows
    come from the story's VIEW, so a row of a rewound timeline refuses
    along with another story's: neither is in the memory any browser
    shows."""
    found = next((row for row in rows if row.id == target), None)
    if found is None:
        raise NotFound(f"That {noun} is not in this story's memory.")
    return found
