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
    could carry; for a file, its picture's thumbnail, a JPEG (None for
    none), the codex card it is already (None for none) and the codex
    cards alike — of the same name or picture."""

    name: str
    notes: tuple[str, ...]
    block_tokens: int
    large: bool
    persona: str
    has_picture: bool
    thumbnail: bytes | None
    same_card: CardSummary | None
    similar_cards: tuple[CardSummary, ...]


class StoryCardFetchCheck(StoryCardCheck):
    """A card read from a link and vetted for a story as a file is
    (`StoryCardCheck`), nothing stored, and the file the link gave — its
    name and bytes — with the line its site shows it under ("" for none),
    for the import to send as a card file."""

    file_name: str
    data: bytes
    tagline: str


class StoryCardImport(Schema):
    """What landed: the character the card became and the codex card it
    is linked to (None for a card the story alone holds), the recorded
    /card row, the greeting message (None when the card had none), and
    the one-line report."""

    character_id: int
    card_id: int | None
    line: Message
    greeting: Message | None
    report: str
