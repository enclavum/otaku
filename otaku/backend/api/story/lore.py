"""A story's memory as a frontend reaches it: loaded whole
(`LoreResource`), its scenes, characters and journal records each read
and corrected, a character merged into another or deleted, and the
forced extraction pass that builds it — started, read until done, given
up. The work is the domain's (`domain.story.lore`); this answers what
stands after it, every derived fact (a scene's number and span, a
character's arc so far, how far the story has moved past a record)
filled in.
"""

from __future__ import annotations

from dataclasses import dataclass

from otaku.backend.api._writes import UNCHANGED, Changed, Unchanged
from otaku.backend.domain.app import App
from otaku.backend.domain.story import lore as domain_lore
from otaku.backend.domain.story.lore import ExtractionState, Memory
from otaku.store import schema


class LoreResource:
    """/stories/{story}/lore — a story's memory, loaded whole."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, story_id: int) -> Lore:
        """A story's memory: its current scenes, the cast in order of
        appearance, the current timeline's journal records, and how far
        the extraction has read. Raises NotFound for a story that is not
        there."""
        held = domain_lore.get_lore(self._app, story_id)
        return Lore(
            story_id=held.story_id,
            message_count=held.total_messages,
            extracted_up_to=held.extracted_up_to(),
            scenes=tuple(_scene(held, row) for row in held.scenes),
            characters=tuple(_character(held, row) for row in held.cast),
            journals=tuple(_journal(held, row) for row in held.journals),
        )


class ScenesResource:
    """/stories/{story}/scenes — a story's scenes, each by its id."""

    def __init__(self, app: App) -> None:
        self._app = app

    def list(self, story_id: int) -> list[Scene]:
        """A story's current scenes, in story order. Raises NotFound for a
        story that is not there."""
        held = domain_lore.get_lore(self._app, story_id)
        return [_scene(held, row) for row in held.scenes]

    def get(self, story_id: int, scene_id: int) -> Scene:
        """One scene of a story's memory. Raises NotFound for one that is
        not in it — another story's, or a rewound timeline's."""
        held = domain_lore.get_lore(self._app, story_id)
        return _scene(held, held.scene(scene_id))

    def update(
        self,
        story_id: int,
        scene_id: int,
        *,
        title: str | Unchanged = UNCHANGED,
        summary: str | Unchanged = UNCHANGED,
    ) -> Changed[Scene]:
        """The author's correction of a scene, checked to be that
        story's. Its arc (`history`) is derived and has no write. Raises
        Refused for nothing to change, an emptied summary, and a scene not
        in the story's memory — nothing written."""
        said = domain_lore.update_scene(
            self._app,
            story_id,
            scene_id,
            title=None if isinstance(title, Unchanged) else title,
            summary=None if isinstance(summary, Unchanged) else summary,
        )
        return Changed(self.get(story_id, scene_id), said)


class CharactersResource:
    """/stories/{story}/characters — a story's cast, each by its id."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.merges = MergesResource(app)

    def list(self, story_id: int, *, name: str | None = None) -> list[Character]:
        """A story's characters in appearance order — what the prompt
        menus and the pickers ask while a name is typed — or, by `name`,
        the one a typed name or alias resolves to, case-insensitively
        (none for none). Empty for a story that is not there: a menu
        question is never a refusal."""
        rows = domain_lore.list_characters(self._app, story_id, name=name)
        if not rows:
            return []
        held = domain_lore.get_lore(self._app, story_id)
        return [_character(held, row) for row in rows]

    def get(self, story_id: int, character_id: int) -> Character:
        """One character of a story's cast. Raises NotFound for one that
        is not in it."""
        held = domain_lore.get_lore(self._app, story_id)
        return _character(held, held.character(character_id))

    def update(
        self,
        story_id: int,
        character_id: int,
        *,
        description: str | Unchanged = UNCHANGED,
        card: str | Unchanged = UNCHANGED,
    ) -> Changed[Character]:
        """The author's correction of a character — its description, and
        the card archive an imported one carries. Raises Refused for
        nothing to change, a card that is no TOML, and a character not in
        the cast — nothing written."""
        said = domain_lore.update_character(
            self._app,
            story_id,
            character_id,
            description=None if isinstance(description, Unchanged) else description,
            card=None if isinstance(card, Unchanged) else card,
        )
        return Changed(self.get(story_id, character_id), said)

    def delete(self, story_id: int, character_id: int) -> Changed[None]:
        """Remove a character the extractor should never have made, their
        journals with them, and their name from every line they spoke.
        Refused for a character carrying an imported card."""
        return Changed(None, domain_lore.delete_character(self._app, story_id, character_id))


class MergesResource:
    """/stories/{story}/characters/{character}/merges — a character
    folded into another."""

    def __init__(self, app: App) -> None:
        self._app = app

    def create(self, story_id: int, character_id: int, *, into: int) -> Changed[Character]:
        """Fold a story's character into `into` — speakers and journals
        follow, the one folded becomes an alias — answered with the
        character it went into as it now stands. Refused toward the
        direction that would destroy an imported card's archive."""
        said = domain_lore.merge_characters(self._app, story_id, character_id, into=into)
        held = domain_lore.get_lore(self._app, story_id)
        return Changed(_character(held, held.character(into)), said)


class JournalsResource:
    """/stories/{story}/journals — a story's journal records, each by its
    id."""

    def __init__(self, app: App) -> None:
        self._app = app

    def list(self, story_id: int) -> list[Journal]:
        """The journal records of a story's current timeline. Raises
        NotFound for a story that is not there."""
        held = domain_lore.get_lore(self._app, story_id)
        return [_journal(held, row) for row in held.journals]

    def get(self, story_id: int, journal_id: int) -> Journal:
        """One journal record of a story's memory. Raises NotFound for one
        that is not in it."""
        held = domain_lore.get_lore(self._app, story_id)
        return _journal(held, held.journal(journal_id))

    def update(
        self, story_id: int, journal_id: int, *, entry: str | Unchanged = UNCHANGED
    ) -> Changed[Journal]:
        """The author's correction of a record's entry; the state is the
        extractor's own and has no write. Raises Refused for nothing to
        change, and a record not in the story's memory."""
        said = domain_lore.update_journal(
            self._app,
            story_id,
            journal_id,
            entry=None if isinstance(entry, Unchanged) else entry,
        )
        return Changed(self.get(story_id, journal_id), said)


class ExtractionResource:
    """/stories/{story}/extraction — the story's forced extraction pass."""

    def __init__(self, app: App) -> None:
        self._app = app

    def create(self, story_id: int) -> Changed[Extraction]:
        """Run the extraction over a story right now — gate and settle
        margin dropped, closing right up to the last message — answered
        at once, running, for a client to read again until it is done; a
        pass already running is the one answered. Raises NotFound for a
        story that is not there, Conflict without a model to extract
        with."""
        state, said = domain_lore.create_extraction(self._app, story_id)
        return Changed(_extraction(story_id, state), said)

    def get(self, story_id: int) -> Extraction:
        """A story's forced pass as it stands — the one running, or the
        last one asked for. Safe from any thread: a run is read without
        the app's. Raises NotFound where none was asked for."""
        return _extraction(story_id, domain_lore.get_extraction(self._app, story_id))

    def delete(self, story_id: int) -> Changed[None]:
        """Give up a story's forced pass — nothing half-done commits;
        already-closed scenes stay. Refused where none is running: a pass
        can finish between the asking and this, and an automatic pass is
        the worker's own, which nobody asked for and nobody cancels."""
        return Changed(None, domain_lore.delete_extraction(self._app, story_id))


def _scene(held: Memory, row: schema.Scene) -> Scene:
    return Scene(
        id=row.id,
        no=held.scene_no(row.id),
        span=held.scene_span(row.id),
        title=row.title,
        summary=row.summary,
        history=row.history,
        start_message_id=row.start_message_id,
        end_message_id=row.end_message_id,
        provider=row.provider,
        model=row.model,
        updated_at=row.updated_at,
    )


def _character(held: Memory, row: schema.Character) -> Character:
    history, holder = held.character_history(row.id)
    return Character(
        id=row.id,
        name=row.name,
        aliases=row.aliases,
        description=row.description,
        card=row.card,
        history=history,
        history_journal_id=holder.id if holder is not None else None,
        provider=row.provider,
        model=row.model,
        updated_at=row.updated_at,
    )


def _journal(held: Memory, row: schema.Journal) -> Journal:
    return Journal(
        id=row.id,
        scene_id=row.scene_id,
        scene_no=held.scene_no(row.scene_id),
        character_id=row.character_id,
        entry=row.entry,
        state=row.state,
        history=row.history,
        messages_since=held.messages_since(row.scene_id),
        provider=row.provider,
        model=row.model,
        updated_at=row.updated_at,
    )


def _extraction(story_id: int, state: ExtractionState) -> Extraction:
    return Extraction(story_id=story_id, status=state.status, report=state.report)


@dataclass(frozen=True)
class Lore:
    """A story's memory as the browser shows it: how many messages its
    chain holds, how far the extraction has read (`extracted_up_to`, a
    1-based chain position, 0 for nothing yet — what lies past it is no
    fault: the settle margin leaves the newest messages open on
    purpose), its current scenes in story order, the cast in order of
    appearance, and the current timeline's journal records."""

    story_id: int
    message_count: int
    extracted_up_to: int
    scenes: tuple[Scene, ...]
    characters: tuple[Character, ...]
    journals: tuple[Journal, ...]


@dataclass(frozen=True)
class Scene:
    """One scene of a story's memory: its number in story order (0 for
    one not in the memory) and its "N-M" message range on the current
    chain ("" where either end is off it), its title, summary and arc
    (the story so far through it; derived, "" where not generated here),
    the messages it starts and ends at, who made it, and when it was
    last written (for display alone)."""

    id: int
    no: int
    span: str
    title: str
    summary: str
    history: str
    start_message_id: int
    end_message_id: int
    provider: str | None
    model: str | None
    updated_at: str


@dataclass(frozen=True)
class Character:
    """One character of a story's cast: its name and aliases, its
    description, the card archive an imported one carries (None for one
    the extractor made), its arc so far — the newest non-empty rollup,
    "" before a pass has written one — and the id of the journal record
    it was taken from (where a frontend says how old it is), who made
    it, and when it was last written (for display alone)."""

    id: int
    name: str
    aliases: tuple[str, ...]
    description: str
    card: str | None
    history: str
    history_journal_id: int | None
    provider: str | None
    model: str | None
    updated_at: str


@dataclass(frozen=True)
class Journal:
    """One journal record: the scene it belongs to and that scene's
    number, the character it is about, the entry (the author's to
    correct), the state and the arc (the extractor's own), how far the
    story has moved past its scene, in messages, who made it, and when it
    was last written (for display alone)."""

    id: int
    scene_id: int
    scene_no: int
    character_id: int
    entry: str
    state: str
    history: str
    messages_since: int
    provider: str | None
    model: str | None
    updated_at: str


@dataclass(frozen=True)
class Extraction:
    """A story's forced extraction pass as it stands: its `status` —
    "running", "done", "cancelled" or "failed" — and, once it is not
    running, its one-line `report`: scenes closed, journals written,
    too little new play yet, or what failed and why."""

    story_id: int
    status: str
    report: str | None
