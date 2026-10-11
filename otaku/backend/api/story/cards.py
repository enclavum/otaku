"""Character cards as a frontend brings them in (`CardsResource`): one
imported into a story — or, asked to validate only, read and vetted for
the story and nothing stored, what an ask before the import shows. The
import itself is the domain's (`domain.story.cards`); this answers its
two rows as messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, overload

from otaku.backend.api._writes import UNCHANGED, Changed, Unchanged
from otaku.backend.api.story.messages import Message, MessagesResource
from otaku.backend.domain.app import App
from otaku.backend.domain.story import cards as domain_cards
from otaku.backend.errors import Invalid


class CardsResource:
    """/stories/{story}/cards — character cards brought into a story."""

    def __init__(self, app: App) -> None:
        self._app = app

    @overload
    def create(
        self,
        story_id: int,
        *,
        data: bytes,
        file_name: str,
        rename: str = "",
        validate_only: Literal[True],
    ) -> CardCheck: ...

    @overload
    def create(
        self,
        story_id: int,
        *,
        data: bytes,
        file_name: str,
        rename: str = "",
        persona: str,
        line: str = "",
        expected_head: int | None,
        validate_only: Literal[False] = False,
    ) -> Changed[ImportedCard]: ...

    def create(
        self,
        story_id: int,
        *,
        data: bytes,
        file_name: str,
        rename: str = "",
        persona: str = "",
        line: str = "",
        expected_head: int | Unchanged | None = UNCHANGED,
        validate_only: bool = False,
    ) -> CardCheck | Changed[ImportedCard]:
        """A character card imported into a story from its FILE's bytes —
        or, with `validate_only`, the card read and vetted for the story
        and nothing stored (`CardCheck`: what an ask before the import
        shows, the persona the story remembers its default). `rename`
        overrides the card's own name. Bytes-shaped on purpose: resolving
        a path is the frontend's — the web uploads. The import is played
        on from `expected_head`, the head the caller read: the typed row,
        the character with the card's archive, the greeting message; `/undo`
        takes the exchange whole. `line` is what the story RECORDS; an
        upload has only a file name, which is what the default composes.
        Raises Refused — not a card, a cast name already taken, a head
        standing elsewhere."""
        app = self._app
        if validate_only:
            checked = domain_cards.check_card(app, story_id, data, file_name, rename=rename)
            return CardCheck(
                name=checked.card.name,
                notes=checked.notes,
                block_tokens=checked.block_tokens,
                large=checked.large,
                persona=checked.persona,
            )
        if isinstance(expected_head, Unchanged):
            raise Invalid("A card import names the head it plays on from.")
        recorded, greeting, report = domain_cards.import_card(
            app,
            story_id,
            data,
            file_name,
            rename=rename,
            persona=persona,
            line=line,
            expected_head=expected_head,
        )
        # Both rows as a client draws them, each joined to the one before it.
        drawn = {message.id: message for message in MessagesResource(app).list(story_id)}
        assert recorded.speaker_id is not None  # the import links the row to its character
        imported = ImportedCard(
            character_id=recorded.speaker_id,
            line=drawn[recorded.id],
            greeting=drawn[greeting.id] if greeting is not None else None,
            report=report,
        )
        return Changed(imported, report)


@dataclass(frozen=True)
class CardCheck:
    """A card read and vetted for a story, nothing stored: the name it
    goes by, what the import should say out loud, the composed block's
    cost estimate and whether it is worth warning about — it rides
    every request verbatim — and the persona the story remembers, the
    ask's default ("" for none)."""

    name: str
    notes: tuple[str, ...]
    block_tokens: int
    large: bool
    persona: str


@dataclass(frozen=True)
class ImportedCard:
    """What landed: the character the card became, the recorded /card
    row, the greeting message (None when the card had none), and the
    one-line report."""

    character_id: int
    line: Message
    greeting: Message | None
    report: str
