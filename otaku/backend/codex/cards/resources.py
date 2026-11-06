"""The codex's character cards as a frontend reaches them
(`CardsResource`): listed, in the reader's order, and read; made by
hand, from a card file or a picture that holds none — or, asked to
validate only, the file read and vetted and nothing stored, with the
cards the codex may already hold it as — or from a story's character,
promoted and linked to it, its copies in forked stories with it where
asked (and vetted first, to count them), each going last; corrected,
moved in the order, its pictures ordered and removed; deleted. A card
fetched from a link (`CardFetchesResource`), a picture added to a
card (`CardPicturesResource`), a card written by the model in use from
its pictures (`CardGenerationResource`), a card folded into another
(`CardMergesResource`), and every story's character no card claims
(`UnlinkedResource`). The work is the domain's
(`codex.cards.services`); this answers what stands after it.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Literal, overload

from otaku.backend.app.core.services import App
from otaku.backend.app.pictures.services import RawFile
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged, any_thread
from otaku.backend.codex.cards import services as domain_cards
from otaku.backend.codex.cards.schemas import (
    Card,
    CardCheck,
    CardFetchCheck,
    CardImport,
    CardSummary,
    CharacterLinkCheck,
    StoryCharacter,
    as_card_summary,
)
from otaku.backend.errors import Invalid
from otaku.context.cards import CharacterProfile
from otaku.store import schema


class CardsResource(Resource):
    """/cards — the codex's character cards, each by its id."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.fetches = CardFetchesResource(app)
        self.pictures = CardPicturesResource(app)
        self.generation = CardGenerationResource(app)
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
        self,
        *,
        data: bytes,
        file_name: str,
        name: str | None = None,
        validate_only: Literal[True],
    ) -> CardCheck: ...

    @overload
    def create(
        self,
        *,
        data: bytes,
        file_name: str,
        name: str,
        reuse: int | None = None,
        tagline: str = "",
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
        reuse: int | None = None,
        story_id: int | None = None,
        character_id: int | None = None,
        with_copies: bool = False,
        validate_only: bool = False,
    ) -> Changed[Card] | Changed[CardImport] | CardCheck | CharacterLinkCheck:
        """A card made three ways. By hand, from `name` — none given, the
        card is called `NEW_CARD_NAME` for the reader to replace — and its
        fields. From a card FILE's bytes (`data`, the `file_name` it was
        picked as, `name` the one it is made under — asked of an import,
        and on a check vetted where given, the file's own answered where
        not): a file the codex holds already makes nothing and answers
        that card, `reuse` names the
        card the reader says it is, and otherwise a card is made, the
        picture the file carries its portrait — a PNG's own, a CHARX's
        main icon (`CardImport.made` says which) — and `tagline` its own,
        a site's where the file came from a link; a picture
        that holds no card makes a blank card, the picture its portrait —
        or, with `validate_only`, the file read and vetted and nothing
        stored (`CardCheck`). From a story's character (`story_id`,
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
                checked = domain_cards.check_card_file(
                    app, data, file_name, name=name, allow_bare_picture=True
                )
                story_counts = domain_cards.get_story_counts(app)
                return CardCheck(
                    name=checked.profile.name,
                    notes=checked.notes,
                    has_picture=checked.has_picture,
                    thumbnail=checked.thumbnail,
                    same_card=(
                        as_card_summary(checked.same_card, story_counts)
                        if checked.same_card is not None
                        else None
                    ),
                    similar_cards=tuple(
                        as_card_summary(row, story_counts) for row in checked.similar_cards
                    ),
                    similar_note=domain_cards.similar_note(checked),
                )
            card_id, made, said = domain_cards.import_card_file(
                app,
                data,
                file_name,
                name=name,
                reuse=reuse,
                tagline=tagline,
                allow_bare_picture=True,
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
        correcting = bool(changes) or any(not isinstance(p, Unchanged) for p in corrected.values())
        moving = not isinstance(before, Unchanged)
        if correcting and not isinstance(before, Unchanged):
            # a move that cannot be made refuses the correction with it
            domain_cards.check_move(self._app, card_id, before=before)
        said = []
        # given nothing at all, the domain's update refuses it
        if correcting or not moving:
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
        return Changed(self.get(card_id), " ".join(said))

    def delete(self, card_id: int) -> Changed[None]:
        """A card removed from the codex, its pictures with it; the story
        characters linked to it stay, unlinked and otherwise untouched."""
        return Changed(None, domain_cards.delete_card(self._app, card_id))


class CardFetchesResource(Resource):
    """/cards/fetches — a card fetched from a link into the codex."""

    @overload
    def create(self, *, url: str, validate_only: Literal[False] = False) -> Changed[CardImport]: ...

    @overload
    def create(self, *, url: str, validate_only: Literal[True]) -> CardFetchCheck: ...

    @any_thread
    def create(
        self, *, url: str, validate_only: bool = False
    ) -> Changed[CardImport] | CardFetchCheck:
        """The card a link gives — a card site's character page, or the
        file's own link, a picture alone included — fetched and
        imported as a card file is (`CardsResource.create`): a card the
        codex holds already makes nothing and answers that card. With
        `validate_only`, fetched and vetted, nothing stored, and
        answered with the file and the site's tagline too
        (`CardFetchCheck`), for the import to send as a card file — so
        a link read before it is imported is fetched once. A card made
        takes the line its site shows it under, where the site has one.
        Safe from any thread, and slow on purpose: the site is asked off
        the app's thread, the card read and written on it. Raises Refused
        as a file's import does, and for a link no card is fetched from
        or a site that could not be reached."""
        file = domain_cards.fetch_card(self._app, url)
        cards = CardsResource(self._app)
        check = cards.create(data=file.data, file_name=file.name, validate_only=True)
        if not validate_only:
            return cards.create(
                data=file.data, file_name=file.name, name=check.name, tagline=file.tagline
            )
        return CardFetchCheck(
            **{each.name: getattr(check, each.name) for each in fields(check)},
            file_name=file.name,
            data=file.data,
            tagline=file.tagline,
        )


class CardPicturesResource(Resource):
    """/cards/{card}/pictures — a card's pictures, added and removed one
    at a time; ordered through the card itself."""

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


class CardGenerationResource(Resource):
    """/cards/{card}/generation — a card written by the model in use from
    its pictures, and that writing given up."""

    @any_thread
    def create(
        self, card_id: int, *, language: str = domain_cards.DEFAULT_LANGUAGE
    ) -> Changed[Card]:
        """The card written from its pictures — every one — in `language`,
        its English name: its tagline, description, personality, scenario
        and greeting replaced, its name while it is still `NEW_CARD_NAME`.
        Safe from any thread, and slow
        on purpose: the model is asked off the app's thread, the card read
        and written on it. Raises NotFound for a card the codex does not
        hold; Invalid for a language that is no short line; Conflict while
        no model is in use, the model cannot see pictures, or the card has
        none; Unavailable for what the engine refuses and an answer that
        is no card."""
        app = self._app
        said = domain_cards.generate_card(app, card_id, language=language)
        return Changed(
            app.run(lambda: _card(app, domain_cards.get_card(app, card_id)), reading=True), said
        )

    @any_thread
    def delete(self, card_id: int) -> Changed[None]:
        """The card's generation given up — the model's ask cut, nothing
        written. Safe from any thread: it sets the run's flag alone, so a
        reply holding the app's thread never holds it. Raises Conflict
        where none is running."""
        return Changed(None, domain_cards.cancel_generation(self._app, card_id))


class CardMergesResource(Resource):
    """/cards/{card}/merges — a card folded into another."""

    def create(self, card_id: int, *, into: int) -> Changed[Card]:
        """Fold a card into `into` — its story characters follow, its name
        becomes an alias, its pictures follow — answered with the card it
        went into as it now stands. Refused where one story holds
        characters linked to both."""
        said = domain_cards.merge_cards(self._app, card_id, into=into)
        return Changed(CardsResource(self._app).get(into), said)


class UnlinkedResource(Resource):
    """/cards/unlinked — every story's character no card claims."""

    def list(self, *, query: str = "") -> list[StoryCharacter]:
        """Every story's character no card claims — or those whose name, an
        alias or a journal record's entry or state holds `query`, cased as
        the reader likes — the most written about first."""
        return [_story_character(row) for row in domain_cards.list_unlinked(self._app, query=query)]


def _card(app: App, row: schema.Card) -> Card:
    profile = domain_cards.card_profile(row)
    return Card(
        id=row.id,
        name=row.name,
        aliases=row.aliases,
        tagline=row.tagline,
        **{name: getattr(profile, name) for name in domain_cards.FIELDS},
        file_name=profile.file_name,
        pictures=row.pictures,
        stories=tuple(_story_character(r) for r in domain_cards.list_linked(app, row.id)),
        is_blank=domain_cards.is_blank(profile),
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
