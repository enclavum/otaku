"""Character cards as a frontend brings them into a story
(`StoryCardsResource`), always through the codex: a card file, which
goes into the codex first, or a codex card, invited — or, asked to
validate only, read and vetted for the story and nothing stored, what an
ask before the import shows. The work is the domain's
(`domain.story.cards`); this answers its two rows as messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, overload

from otaku.backend.api._writes import UNCHANGED, Changed, Unchanged
from otaku.backend.api.codex.cards import CardSummary, as_card_summary
from otaku.backend.api.story.messages import Message, MessagesResource
from otaku.backend.domain.app import App
from otaku.backend.domain.codex.cards import get_story_counts
from otaku.backend.domain.story import cards as domain_cards
from otaku.backend.errors import Invalid


class StoryCardsResource:
    """/stories/{story}/cards — character cards brought into a story."""

    def __init__(self, app: App) -> None:
        self._app = app

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
                name=checked.card.name,
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


@dataclass(frozen=True)
class StoryCardCheck:
    """A card read and vetted for a story, nothing stored: the name it
    goes by, what the import should say out loud, the composed block's
    cost estimate and whether it is worth warning about — it rides every
    request verbatim — the persona the story remembers, the ask's
    default ("" for none), and whether the card has a portrait its row
    could carry; for a file, the codex card it is already (None for
    none) and the codex cards of the same name the reader may say it
    is."""

    name: str
    notes: tuple[str, ...]
    block_tokens: int
    large: bool
    persona: str
    has_picture: bool
    same_card: CardSummary | None
    similar_cards: tuple[CardSummary, ...]


@dataclass(frozen=True)
class ImportedCard:
    """What landed: the character the card became and the codex card it
    is linked to, the recorded /card row, the greeting message (None
    when the card had none), and the one-line report."""

    character_id: int
    card_id: int
    line: Message
    greeting: Message | None
    report: str
