"""A story's memory as a frontend reaches it: loaded whole
(`LoreResource`), its scenes, characters and journal records each read
and corrected, a character merged into another or deleted, and the
forced extraction pass that builds it — started, read until done, given
up. The work is the domain's (`story.lore.services`); this answers what
stands after it, every derived fact (a scene's number and span, a
character's arc so far, how far the story has moved past a record)
filled in.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, overload

from otaku.backend.app.core.services import App
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged, any_thread
from otaku.backend.codex.cards.schemas import CharacterLinkCheck
from otaku.backend.codex.cards.services import FIELDS, story_profile
from otaku.backend.errors import Invalid
from otaku.backend.story.lore import services as domain_lore
from otaku.backend.story.lore.schemas import (
    Character,
    Extraction,
    Journal,
    Lore,
    ProfileFields,
    Scene,
)
from otaku.backend.story.lore.services import ExtractionState, Memory
from otaku.context.cards import CARD_PROVIDER
from otaku.store import schema


class LoreResource(Resource):
    """/stories/{story}/lore — a story's memory, loaded whole."""

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


class ScenesResource(Resource):
    """/stories/{story}/scenes — a story's scenes, each by its id."""

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


class CharactersResource(Resource):
    """/stories/{story}/characters — a story's cast, each by its id."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
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

    @overload
    def update(
        self,
        story_id: int,
        character_id: int,
        *,
        description: str | Unchanged = UNCHANGED,
        profile: str | Unchanged = UNCHANGED,
        card_id: int | Unchanged | None = UNCHANGED,
        profile_fields: Mapping[str, Any] | Unchanged = UNCHANGED,
        portrait_sent: bool | Unchanged = UNCHANGED,
        with_copies: bool = False,
    ) -> Changed[Character]: ...

    @overload
    def update(
        self, story_id: int, character_id: int, *, card_id: int, validate_only: Literal[True]
    ) -> CharacterLinkCheck: ...

    def update(
        self,
        story_id: int,
        character_id: int,
        *,
        description: str | Unchanged = UNCHANGED,
        profile: str | Unchanged = UNCHANGED,
        card_id: int | Unchanged | None = UNCHANGED,
        profile_fields: Mapping[str, Any] | Unchanged = UNCHANGED,
        portrait_sent: bool | Unchanged = UNCHANGED,
        with_copies: bool = False,
        validate_only: bool = False,
    ) -> Changed[Character] | CharacterLinkCheck:
        """The author's correction of a character — its description, and
        the profile an imported one carries, whole or field by field
        (`profile_fields`, by the names `ProfileFields` gives them) — whether
        its card's picture rides the row that brought it in
        (`portrait_sent`), and its link to a codex card (`card_id`; None
        unlinks), which changes nothing else on either. A link takes the
        character's copies in the stories forks join its own to along
        with `with_copies`; asked to `validate_only`, a link alone is vetted,
        nothing stored, and answered with how many copies. Raises Refused for
        nothing to change, a card that is no TOML, a field a card has not
        or a reminder's depth below 1, a character not in the cast or with
        no card, a picture asked to ride where no card row brought the
        character in or there is no picture, no such codex card, and a
        card another of the story's characters is linked to already."""
        if validate_only:
            vetted = (description, profile, profile_fields, portrait_sent)
            if not isinstance(card_id, int) or any(v is not UNCHANGED for v in vetted):
                raise Invalid("Only a link is vetted before it is made.")
            found = domain_lore.check_card_link(self._app, story_id, character_id, card_id)
            return CharacterLinkCheck(copies=len(found))
        said = []
        if not isinstance(description, Unchanged) or not isinstance(profile, Unchanged):
            said.append(
                domain_lore.update_character(
                    self._app,
                    story_id,
                    character_id,
                    description=None if isinstance(description, Unchanged) else description,
                    profile=None if isinstance(profile, Unchanged) else profile,
                )
            )
        if not isinstance(profile_fields, Unchanged):
            said.append(
                domain_lore.update_profile(self._app, story_id, character_id, profile_fields)
            )
        if not isinstance(portrait_sent, Unchanged):
            said.append(
                domain_lore.set_portrait_sent(self._app, story_id, character_id, portrait_sent)
            )
        if not isinstance(card_id, Unchanged):
            said.append(
                domain_lore.set_card_id(
                    self._app, story_id, character_id, card_id, with_copies=with_copies
                )
            )
        if not said:
            raise Invalid("Nothing to change.")
        return Changed(self.get(story_id, character_id), " ".join(said))

    def delete(self, story_id: int, character_id: int) -> Changed[None]:
        """Remove a character the extractor should never have made, their
        journals with them, and their name from every line they spoke.
        Refused for a character carrying an imported card."""
        return Changed(None, domain_lore.delete_character(self._app, story_id, character_id))


class MergesResource(Resource):
    """/stories/{story}/characters/{character}/merges — a character
    folded into another."""

    def create(self, story_id: int, character_id: int, *, into: int) -> Changed[Character]:
        """Fold a story's character into `into` — speakers and journals
        follow, the one folded becomes an alias — answered with the
        character it went into as it now stands. Refused toward the
        direction that would destroy an imported card's profile."""
        said = domain_lore.merge_characters(self._app, story_id, character_id, into=into)
        held = domain_lore.get_lore(self._app, story_id)
        return Changed(_character(held, held.character(into)), said)


class JournalsResource(Resource):
    """/stories/{story}/journals — a story's journal records, each by its
    id."""

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


class ExtractionResource(Resource):
    """/stories/{story}/extraction — the story's forced extraction pass."""

    def create(self, story_id: int) -> Changed[Extraction]:
        """Run the extraction over a story right now — gate and settle
        margin dropped, closing right up to the last message — answered
        at once, running, for a client to read again until it is done; a
        pass already running is the one answered. Raises NotFound for a
        story that is not there, Conflict without a model to extract
        with."""
        state, said = domain_lore.create_extraction(self._app, story_id)
        return Changed(_extraction(story_id, state), said)

    @any_thread
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
    linked = held.linked_cards.get(row.card_id) if row.card_id is not None else None
    return Character(
        id=row.id,
        name=row.name,
        aliases=row.aliases,
        description=row.description,
        profile=row.profile,
        profile_fields=_profile_fields(row),
        history=history,
        history_journal_id=holder.id if holder is not None else None,
        card_id=row.card_id,
        card_name=linked.name if linked is not None else None,
        card_portrait=linked.pictures[0] if linked is not None and linked.pictures else None,
        portrait_sent=held.portraits_sent.get(row.id),
        provider=row.provider,
        # a card made it, not a model: the file it came from is no model's
        # name, and the profile keeps it
        model=None if row.provider == CARD_PROVIDER else row.model,
        updated_at=row.updated_at,
    )


def _profile_fields(row: schema.Character) -> ProfileFields | None:
    profile = story_profile(row)
    if profile is None:
        return None
    return ProfileFields(**{name: getattr(profile, name) for name in FIELDS})


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
