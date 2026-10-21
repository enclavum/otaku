"""Character cards as a frontend brings them into a story
(`StoryCardsResource`), always through the codex: a card file, which
goes into the codex first, or a codex card, invited — or, asked to
validate only, read and vetted for the story and nothing stored, what an
ask before the import shows. The work is the domain's
(`story.cards.services`); this answers its two rows as messages.
"""

from __future__ import annotations

from typing import Literal, overload

from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged
from otaku.backend.codex.cards.schemas import as_card_summary
from otaku.backend.codex.cards.services import get_story_counts
from otaku.backend.errors import Invalid
from otaku.backend.story.cards import services as domain_cards
from otaku.backend.story.cards.schemas import ImportedCard, StoryCardCheck
from otaku.backend.story.messages.resources import MessagesResource


class StoryCardsResource(Resource):
    """/stories/{story}/cards — character cards brought into a story."""

    @overload
    def create(
        self,
        story_id: int,
        *,
        data: bytes | None = None,
        file_name: str = "",
        card_id: int | None = None,
        rename: str = "",
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
        rename: str = "",
        reuse: int | None = None,
        persona: str,
        with_pictures: bool = False,
        line: str = "",
        expected_head: int | None,
        validate_only: Literal[False] = False,
    ) -> Changed[ImportedCard]: ...

    def create(
        self,
        story_id: int,
        *,
        data: bytes | None = None,
        file_name: str = "",
        card_id: int | None = None,
        rename: str = "",
        reuse: int | None = None,
        persona: str = "",
        with_pictures: bool = False,
        line: str = "",
        expected_head: int | Unchanged | None = UNCHANGED,
        validate_only: bool = False,
    ) -> StoryCardCheck | Changed[ImportedCard]:
        """A character card brought into a story — from a card FILE's bytes
        (`data`, the `file_name` it was picked as), which goes into the
        codex first: a file the codex holds already brings its card in,
        and `reuse` names the codex card the reader says it is; or a
        codex card (`card_id`), invited as it stands. `rename` overrides
        the card's name. With `validate_only`, the card is read and
        vetted for the story and nothing stored (`StoryCardCheck`: what
        an ask before the import shows, the persona the story remembers
        its default). Bytes-shaped on purpose: resolving a path is the
        frontend's — the web uploads. The card is played on from
        `expected_head`, the head the caller read: the typed row —
        carrying the card's portrait where `with_pictures` asks and it has
        one — the character with the card's fields, linked to the codex
        card, the greeting message; `/undo` takes the exchange whole, the
        codex card staying. `line` is what the story RECORDS; the default
        names the file, or the codex card. Raises Refused — not a card,
        no such codex card, a cast name already taken, the card in the
        story already, a head standing elsewhere."""
        app = self._app
        if validate_only:
            checked = domain_cards.check_card(
                app, story_id, data=data, file_name=file_name, card_id=card_id, rename=rename
            )
            story_counts = get_story_counts(app)
            return StoryCardCheck(
                name=checked.profile.name,
                notes=checked.notes,
                block_tokens=checked.block_tokens,
                large=checked.large,
                persona=checked.persona,
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
        if isinstance(expected_head, Unchanged):
            raise Invalid("A card import names the head it plays on from.")
        brought, report = domain_cards.import_card(
            app,
            story_id,
            data=data,
            file_name=file_name,
            card_id=card_id,
            rename=rename,
            reuse=reuse,
            persona=persona,
            with_pictures=with_pictures,
            line=line,
            expected_head=expected_head,
        )
        # Both rows as a client draws them, each joined to the one before it.
        drawn = {message.id: message for message in MessagesResource(app).list(story_id)}
        imported = ImportedCard(
            character_id=brought.character_id,
            card_id=brought.card_id,
            line=drawn[brought.line.id],
            greeting=drawn[brought.greeting.id] if brought.greeting is not None else None,
            report=report,
        )
        return Changed(imported, report)
