"""What `story.cards.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema
from otaku.backend.codex.cards.schemas import CardSummary
from otaku.backend.story.messages.schemas import Message


class StoryCardCheck(Schema):
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


class ImportedCard(Schema):
    """What landed: the character the card became and the codex card it
    is linked to, the recorded /card row, the greeting message (None
    when the card had none), and the one-line report."""

    character_id: int
    card_id: int
    line: Message
    greeting: Message | None
    report: str
