"""The codex's character cards as a frontend reaches them
(`CardsResource`): listed, in the reader's order, and read; made by
hand, from a card file — or, asked to validate only, the file read and
vetted and nothing stored, with the cards the codex may already hold it
as — or from a story's character, promoted and linked to it, its copies
in forked stories with it where asked (and vetted first, to count them),
each going last; corrected, moved in the order, its pictures ordered and removed;
deleted. A picture added to a card (`CardPicturesResource`),
a card folded into another (`CardMergesResource`), and every story's
character no card claims (`UnlinkedResource`). The work is the domain's
(`domain.codex.cards`); this answers what stands after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, overload

from otaku.backend.api._writes import UNCHANGED, Changed, Unchanged
from otaku.backend.domain.app import App
from otaku.backend.domain.app.pictures import RawFile as RawFile
from otaku.backend.domain.codex import cards as domain_cards
from otaku.backend.errors import Invalid
from otaku.context.cards import CharacterProfile
from otaku.store import schema
from otaku.store.schema import Attachment


class CardsResource:
    """/cards — the codex's character cards, each by its id."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.pictures = CardPicturesResource(app)
        self.merges = CardMergesResource(app)
        self.unlinked = UnlinkedResource(app)

    def list(self, *, query: str = "") -> list[CardSummary]:
        """Every card in the reader's order — or those whose name, an
        alias or the tagline holds `query`, cased as the reader likes."""
        story_counts = domain_cards.get_story_counts(self._app)
        return [
            as_card_summary(row, story_counts)
            for row in domain_cards.list_cards(self._app, query=query)
        ]

    def get(self, card_id: int) -> Card:
        """One card whole. Raises NotFound for a card the codex does not
        hold."""
        return _card(self._app, domain_cards.get_card(self._app, card_id))

    @overload
    def create(
        self,
        *,
        name: str | None = None,
        aliases: tuple[str, ...] = (),
        tagline: str = "",
        description: str = "",
        personality: str = "",
        scenario: str = "",
        greeting: str = "",
        alternate_greetings: tuple[str, ...] = (),
        examples: tuple[str, ...] = (),
        reminder: str = "",
        reminder_depth: int = 2,
        system_prompt: str = "",
        post_history_instructions: str = "",
        creator_notes: str = "",
    ) -> Changed[Card]: ...

    @overload
    def create(
        self, *, data: bytes, file_name: str, rename: str = "", validate_only: Literal[True]
    ) -> CardCheck: ...

    @overload
    def create(
        self,
        *,
        data: bytes,
        file_name: str,
        rename: str = "",
        reuse: int | None = None,
        validate_only: Literal[False] = False,
    ) -> Changed[CardImport]: ...

    @overload
    def create(
        self, *, story_id: int, character_id: int, with_copies: bool = False
    ) -> Changed[Card]: ...

    @overload
    def create(
        self, *, story_id: int, character_id: int, validate_only: Literal[True]
    ) -> CharacterLinkCheck: ...

    def create(
        self,
        *,
        name: str | None = None,
        aliases: tuple[str, ...] = (),
        tagline: str = "",
        description: str = "",
        personality: str = "",
        scenario: str = "",
        greeting: str = "",
        alternate_greetings: tuple[str, ...] = (),
        examples: tuple[str, ...] = (),
        reminder: str = "",
        reminder_depth: int = 2,
        system_prompt: str = "",
        post_history_instructions: str = "",
        creator_notes: str = "",
        data: bytes | None = None,
        file_name: str = "",
        rename: str = "",
        reuse: int | None = None,
        story_id: int | None = None,
        character_id: int | None = None,
        with_copies: bool = False,
        validate_only: bool = False,
    ) -> Changed[Card] | Changed[CardImport] | CardCheck | CharacterLinkCheck:
        """A card made three ways. By hand, from `name` — none given, the
        card is called `NEW_CARD_NAME` for the reader to replace — and its
        fields. From a card FILE's bytes (`data`, the `file_name` it was picked
        as; `rename` overrides its own name): a file the codex holds
        already makes nothing and answers that card, `reuse` names the
        card the reader says it is, and otherwise a card is made, a PNG's
        picture its portrait (`CardImport.made` says which) — or, with
        `validate_only`, the file read and vetted and nothing stored
        (`CardCheck`). From a story's character (`story_id`,
        `character_id`): promoted — its name, aliases and fields — and
        linked to it, and with `with_copies` to its copies in the stories forks
        join its own to — or, with `validate_only`, vetted and nothing
        stored, answered with how many copies (`CharacterLinkCheck`). Raises Refused —
        an empty name, a tagline of more than one line, a reminder's depth
        below 1, a file that is no card, a character linked already."""
        app = self._app
        if story_id is not None and character_id is not None:
            if validate_only:
                found = domain_cards.check_promotion(app, story_id, character_id)
                return CharacterLinkCheck(copies=len(found))
            card_id, said = domain_cards.promote_character(
                app, story_id, character_id, with_copies=with_copies
            )
            return Changed(self.get(card_id), said)
        if data is not None:
            if validate_only:
                checked = domain_cards.check_card_file(app, data, file_name, rename=rename)
                story_counts = domain_cards.get_story_counts(app)
                return CardCheck(
                    name=checked.profile.name,
                    notes=checked.notes,
                    has_picture=checked.has_picture,
                    same_card=(
                        as_card_summary(checked.same_card, story_counts)
                        if checked.same_card is not None
                        else None
                    ),
                    similar_cards=tuple(
                        as_card_summary(row, story_counts) for row in checked.similar_cards
                    ),
                )
            card_id, made, said = domain_cards.import_card_file(
                app, data, file_name, rename=rename, reuse=reuse
            )
            return Changed(CardImport(card=self.get(card_id), made=made), said)
        if validate_only:
            raise Invalid("Only a card file or a promotion is vetted before it is made.")
        profile = CharacterProfile(
            name=domain_cards.NEW_CARD_NAME if name is None else name,
            description=description,
            personality=personality,
            scenario=scenario,
            greeting=greeting,
            alternate_greetings=alternate_greetings,
            examples=examples,
            reminder=reminder,
            reminder_depth=reminder_depth,
            system_prompt=system_prompt,
            post_history_instructions=post_history_instructions,
            creator_notes=creator_notes,
        )
        card_id, said = domain_cards.create_card(
            app, profile=profile, aliases=aliases, tagline=tagline
        )
        return Changed(self.get(card_id), said)

    def update(
        self,
        card_id: int,
        *,
        name: str | Unchanged = UNCHANGED,
        aliases: tuple[str, ...] | Unchanged = UNCHANGED,
        tagline: str | Unchanged = UNCHANGED,
        description: str | Unchanged = UNCHANGED,
        personality: str | Unchanged = UNCHANGED,
        scenario: str | Unchanged = UNCHANGED,
        greeting: str | Unchanged = UNCHANGED,
        alternate_greetings: tuple[str, ...] | Unchanged = UNCHANGED,
        examples: tuple[str, ...] | Unchanged = UNCHANGED,
        reminder: str | Unchanged = UNCHANGED,
        reminder_depth: int | Unchanged = UNCHANGED,
        system_prompt: str | Unchanged = UNCHANGED,
        post_history_instructions: str | Unchanged = UNCHANGED,
        creator_notes: str | Unchanged = UNCHANGED,
        pictures: tuple[str, ...] | Unchanged = UNCHANGED,
        before: int | Unchanged | None = UNCHANGED,
    ) -> Changed[Card]:
        """The reader's correction of a card: the parts given, each
        replacing what stood. `pictures` are each of the card's own
        pictures by name, in the order they now stand, the first the
        portrait; `before` moves the card in the reader's order, just
        before that card, or last for None. Raises Refused for nothing to
        change, an emptied name, a tagline of more than one line, a
        reminder's depth below 1, an order that is not the card's
        pictures each once, and a card to go before that the codex does
        not hold, or is this one — nothing written."""
        given = {
            "description": description,
            "personality": personality,
            "scenario": scenario,
            "greeting": greeting,
            "alternate_greetings": alternate_greetings,
            "examples": examples,
            "reminder": reminder,
            "reminder_depth": reminder_depth,
            "system_prompt": system_prompt,
            "post_history_instructions": post_history_instructions,
            "creator_notes": creator_notes,
        }
        changes = {key: value for key, value in given.items() if not isinstance(value, Unchanged)}
        corrected = {
            "name": name,
            "aliases": aliases,
            "tagline": tagline,
            "pictures": pictures,
        }
        if not isinstance(before, Unchanged):
            # a move that cannot be made refuses the correction with it
            domain_cards.check_move(self._app, card_id, before=before)
        said = []
        if changes or any(not isinstance(part, Unchanged) for part in corrected.values()):
            said.append(
                domain_cards.update_card(
                    self._app,
                    card_id,
                    name=None if isinstance(name, Unchanged) else name,
                    aliases=None if isinstance(aliases, Unchanged) else aliases,
                    tagline=None if isinstance(tagline, Unchanged) else tagline,
                    changes=changes or None,
                    pictures=None if isinstance(pictures, Unchanged) else pictures,
                )
            )
        if not isinstance(before, Unchanged):
            said.append(domain_cards.move_card(self._app, card_id, before=before))
        if not said:
            raise Invalid("Nothing to change.")
        return Changed(self.get(card_id), " ".join(said))

    def delete(self, card_id: int) -> Changed[None]:
        """A card removed from the codex, its pictures with it; the story
        characters linked to it stay, unlinked and otherwise untouched."""
        return Changed(None, domain_cards.delete_card(self._app, card_id))


class CardPicturesResource:
    """/cards/{card}/pictures — a card's pictures, added and removed one
    at a time; ordered through the card itself."""

    def __init__(self, app: App) -> None:
        self._app = app

    def create(self, card_id: int, *, file: RawFile) -> Changed[Card]:
        """A picture added to a card, after its others, read through the
        one intake every picture takes. Raises Refused for a picture the
        intake refuses and one the card already has."""
        said = domain_cards.create_card_picture(self._app, card_id, file)
        return Changed(CardsResource(self._app).get(card_id), said)

    def delete(self, card_id: int, name: str) -> Changed[Card]:
        """A picture taken off a card by its file name; the file stays
        while a story's card row it rode names it. Raises Refused for a
        picture that is not the card's."""
        said = domain_cards.delete_card_picture(self._app, card_id, name)
        return Changed(CardsResource(self._app).get(card_id), said)


class CardMergesResource:
    """/cards/{card}/merges — a card folded into another."""

    def __init__(self, app: App) -> None:
        self._app = app

    def create(self, card_id: int, *, into: int) -> Changed[Card]:
        """Fold a card into `into` — its story characters follow, its name
        becomes an alias, its pictures follow — answered with the card it
        went into as it now stands. Refused where one story holds
        characters linked to both."""
        said = domain_cards.merge_cards(self._app, card_id, into=into)
        return Changed(CardsResource(self._app).get(into), said)


class UnlinkedResource:
    """/cards/unlinked — every story's character no card claims."""

    def __init__(self, app: App) -> None:
        self._app = app

    def list(self, *, query: str = "") -> list[StoryCharacter]:
        """Every story's character no card claims — or those whose name, an
        alias or a journal record's entry or state holds `query`, cased as
        the reader likes — the most written about first."""
        return [_story_character(row) for row in domain_cards.list_unlinked(self._app, query=query)]


def as_card_summary(row: schema.Card, story_counts: dict[int, int]) -> CardSummary:
    return CardSummary(
        id=row.id,
        name=row.name,
        aliases=row.aliases,
        tagline=row.tagline,
        pictures=row.pictures,
        story_count=story_counts.get(row.id, 0),
    )


def _card(app: App, row: schema.Card) -> Card:
    profile = domain_cards.profile_of(row)
    return Card(
        id=row.id,
        name=row.name,
        aliases=row.aliases,
        tagline=row.tagline,
        description=profile.description,
        personality=profile.personality,
        scenario=profile.scenario,
        greeting=profile.greeting,
        alternate_greetings=profile.alternate_greetings,
        examples=profile.examples,
        reminder=profile.reminder,
        reminder_depth=profile.reminder_depth,
        system_prompt=profile.system_prompt,
        post_history_instructions=profile.post_history_instructions,
        creator_notes=profile.creator_notes,
        file_name=profile.file_name,
        pictures=row.pictures,
        stories=tuple(_story_character(r) for r in domain_cards.list_linked(app, row.id)),
        updated_at=row.updated_at,
    )


def _story_character(row: domain_cards.StoryCharacter) -> StoryCharacter:
    return StoryCharacter(
        story_id=row.story_id,
        story_label=row.story_label,
        character_id=row.character.id,
        name=row.character.name,
        aliases=row.character.aliases,
        journal_count=row.journal_count,
    )


@dataclass(frozen=True)
class CardSummary:
    """A card as the codex lists it: its name, aliases and tagline, its
    pictures in order — the first the portrait — and how many stories it
    is in, one linked character in each."""

    id: int
    name: str
    aliases: tuple[str, ...]
    tagline: str
    pictures: tuple[Attachment, ...]
    story_count: int


@dataclass(frozen=True)
class Card:
    """One card whole: its name, aliases and tagline (one line for the
    reader, never sent to the model); the card's fields, macros as
    written, and the file it came from ("" for none); its pictures in
    order, the first the portrait; the story characters it is linked to;
    and when it was last written (for display alone)."""

    id: int
    name: str
    aliases: tuple[str, ...]
    tagline: str
    description: str
    personality: str
    scenario: str
    greeting: str
    alternate_greetings: tuple[str, ...]
    examples: tuple[str, ...]
    reminder: str
    reminder_depth: int
    system_prompt: str
    post_history_instructions: str
    creator_notes: str
    file_name: str
    pictures: tuple[Attachment, ...]
    stories: tuple[StoryCharacter, ...]
    updated_at: str


@dataclass(frozen=True)
class StoryCharacter:
    """A story's character as the codex lists it: its story and the label
    a reader finds that story by, its id, name and aliases there, and how
    many journal rows it has — the count a list sorts by, every row on
    any of the story's timelines."""

    story_id: int
    story_label: str
    character_id: int
    name: str
    aliases: tuple[str, ...]
    journal_count: int


@dataclass(frozen=True)
class CardCheck:
    """A card file read and vetted for the codex, nothing stored: the name
    the card would take, what the import should say out loud, whether
    the file carries a picture, the card the codex holds it as already
    (None for none: an import makes a card), and the cards of the same
    name the reader may say it is."""

    name: str
    notes: tuple[str, ...]
    has_picture: bool
    same_card: CardSummary | None
    similar_cards: tuple[CardSummary, ...]


@dataclass(frozen=True)
class CharacterLinkCheck:
    """A link to a card vetted before it is made, nothing stored — a story
    character linked, or promoted to a card of its own: how many copies
    the write takes along when asked to (`with_copies`), in the stories forks
    join the character's own to."""

    copies: int


@dataclass(frozen=True)
class CardImport:
    """What a card file became: the card, and whether it was made now —
    False where the codex held it already, or the reader said which
    card it is."""

    card: Card
    made: bool
