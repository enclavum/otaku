"""Character cards as a frontend brings them into a story
(`StoryCardsResource`), always through the codex: a card file, which
goes into the codex first, or a codex card, invited — or, asked to
validate only, read and vetted for the story and nothing stored, what an
ask before the import shows; and a card file fetched from a link
first (`StoryCardFetchesResource`). The work is the domain's
(`story.cards.services`); this answers its two rows as messages.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Literal, overload

from otaku.backend.app.core.services import App
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged, any_thread
from otaku.backend.codex.cards.schemas import as_card_summary
from otaku.backend.codex.cards.services import (
    check_card_file,
    fetch_card,
    get_story_counts,
    prepare_card_file,
)
from otaku.backend.errors import Invalid
from otaku.backend.story.cards import services as domain_cards
from otaku.backend.story.cards.schemas import (
    StoryCardCheck,
    StoryCardFetchCheck,
    StoryCardImport,
)
from otaku.backend.story.messages.resources import MessagesResource


class StoryCardsResource(Resource):
    """/stories/{story}/cards — character cards brought into a story."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.fetches = StoryCardFetchesResource(app)

    @overload
    def create(
        self,
        story_id: int,
        *,
        data: bytes | None = None,
        file_name: str = "",
        card_id: int | None = None,
        name: str | None = None,
        validate_only: Literal[True],
    ) -> StoryCardCheck: ...

    @overload
    def create(
        self,
        story_id: int,
        *,
        data: bytes | None = None,
        file_name: str = "",
        card_id: int | None = None,
        name: str,
        reuse: int | None = None,
        tagline: str = "",
        persona: str,
        with_pictures: bool = False,
        story_only: bool = False,
        line: str = "",
        expected_head: int | None,
        validate_only: Literal[False] = False,
    ) -> Changed[StoryCardImport]: ...

    @any_thread
    def create(
        self,
        story_id: int,
        *,
        data: bytes | None = None,
        file_name: str = "",
        card_id: int | None = None,
        name: str | None = None,
        reuse: int | None = None,
        tagline: str = "",
        persona: str = "",
        with_pictures: bool = False,
        story_only: bool = False,
        line: str = "",
        expected_head: int | Unchanged | None = UNCHANGED,
        validate_only: bool = False,
    ) -> StoryCardCheck | Changed[StoryCardImport]:
        """A character card brought into a story — from a card FILE's bytes
        (`data`, the `file_name` it was picked as), which goes into the
        codex first: a file the codex holds already brings its card in,
        `reuse` names the codex card the reader says it is, and a card
        made there takes `tagline` (a site's, the file came from a link
        to); or a
        codex card (`card_id`), invited as it stands. `name` is the story's
        character's, asked of an import — on a check vetted where given, the
        card's own answered where not; the codex keeps the card under its
        own name.
        With `story_only`, a file the codex holds not — nor `reuse` names —
        goes into the story alone, its character linked to no card.
        With `validate_only`, the card is read and vetted for the story
        and nothing stored (`StoryCardCheck`: what
        an ask before the import shows, the persona the story remembers
        its default). Bytes-shaped on purpose: resolving a path is the
        frontend's — the web uploads. The card is played on from
        `expected_head`, the head the caller read: the typed row —
        carrying the card's portrait where `with_pictures` asks and it has
        one — the character with the card's fields, linked to the codex
        card, the greeting message; `/undo` takes the exchange whole, the
        codex card staying. `line` is what the story RECORDS; the default
        names the file, or the codex card. Raises Refused — not a card,
        no such codex card, a blank name, a cast name already taken, the
        card in the story already, a head standing elsewhere. Safe from
        any thread: a card file's pictures pass the intake off the app's
        thread, the story's write made on it as one unit; a check runs
        on it whole."""
        app = self._app
        if validate_only:

            def check() -> StoryCardCheck:
                checked = domain_cards.check_card(
                    app, story_id, data=data, file_name=file_name, card_id=card_id, name=name
                )
                story_counts = get_story_counts(app)
                return StoryCardCheck(
                    name=checked.profile.name,
                    notes=checked.notes,
                    block_tokens=checked.block_tokens,
                    large=checked.large,
                    persona=checked.persona,
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
                )

            return app.run(check)
        if isinstance(expected_head, Unchanged):
            raise Invalid("A card import names the head it plays on from.")
        # the slow half off the app's thread; the story's write as one unit on it
        prepared = prepare_card_file(data, file_name) if data is not None else None
        head = expected_head

        def bring_in() -> Changed[StoryCardImport]:
            brought, report = domain_cards.import_card(
                app,
                story_id,
                prepared=prepared,
                card_id=card_id,
                name=name if name is not None else "",
                reuse=reuse,
                tagline=tagline,
                persona=persona,
                with_pictures=with_pictures,
                story_only=story_only,
                line=line,
                expected_head=head,
            )
            # Both rows as a client draws them, each joined to the one before it.
            drawn = {message.id: message for message in MessagesResource(app).list(story_id)}
            imported = StoryCardImport(
                character_id=brought.character_id,
                card_id=brought.card_id,
                line=drawn[brought.line.id],
                greeting=drawn[brought.greeting.id] if brought.greeting is not None else None,
                report=report,
            )
            return Changed(imported, report)

        return app.run(bring_in)


class StoryCardFetchesResource(Resource):
    """/stories/{story}/cards/fetches — a character card fetched from
    a link and brought into a story."""

    @overload
    def create(
        self, story_id: int, *, url: str, name: str | None = None, validate_only: Literal[True]
    ) -> StoryCardFetchCheck: ...

    @overload
    def create(
        self,
        story_id: int,
        *,
        url: str,
        name: str,
        persona: str,
        with_pictures: bool = False,
        line: str = "",
        expected_head: int | None,
        validate_only: Literal[False] = False,
    ) -> Changed[StoryCardImport]: ...

    @any_thread
    def create(
        self,
        story_id: int,
        *,
        url: str,
        name: str | None = None,
        persona: str = "",
        with_pictures: bool = False,
        line: str = "",
        expected_head: int | Unchanged | None = UNCHANGED,
        validate_only: bool = False,
    ) -> StoryCardFetchCheck | Changed[StoryCardImport]:
        """The card a link gives — a card site's character page, or the
        file's own link — fetched and brought in as a card file is
        (`StoryCardsResource.create`) under `name`, the story recording
        `/card URL` — and the name, where it is not the card's own — unless
        `line` says otherwise; with `validate_only`, fetched,
        read and vetted, nothing stored. Safe from any thread, and slow on
        purpose: the site is asked off the app's thread, the card read and
        written on it. Raises Refused as a file's import does, and for a
        link no card is fetched from or a site that could not be
        reached. Checked, it answers the file the link gave too, and the
        line its site shows the card under (`StoryCardFetchCheck`), for
        the import to send as a card file — the link fetched once."""
        cards = StoryCardsResource(self._app)
        if validate_only:
            file = fetch_card(self._app, url)
            check = cards.create(
                story_id, data=file.data, file_name=file.name, name=name, validate_only=True
            )
            return StoryCardFetchCheck(
                **{each.name: getattr(check, each.name) for each in fields(check)},
                file_name=file.name,
                data=file.data,
                tagline=file.tagline,
            )
        if isinstance(expected_head, Unchanged):
            raise Invalid("A card import names the head it plays on from.")
        file = fetch_card(self._app, url)
        # the name the card brings, the codex's where it holds the card — read on the app's thread
        read = self._app.run(lambda: check_card_file(self._app, file.data, file.name))
        own = read.same_card.name if read.same_card is not None else read.profile.name
        asked = (name or "").strip()
        typed = f"/card {url.strip()}" + (f" {asked}" if asked and asked != own else "")
        return cards.create(
            story_id,
            data=file.data,
            file_name=file.name,
            name=name if name is not None else "",
            tagline=file.tagline,
            persona=persona,
            with_pictures=with_pictures,
            line=line or typed,
            expected_head=expected_head,
        )
