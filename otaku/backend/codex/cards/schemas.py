"""What `codex.cards.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.app.pictures.services import RawFile as RawFile
from otaku.backend.base import Schema
from otaku.store import schema
from otaku.store.schema import Attachment


def as_card_summary(row: schema.Card, story_counts: dict[int, int]) -> CardSummary:
    return CardSummary(
        id=row.id,
        name=row.name,
        aliases=row.aliases,
        tagline=row.tagline,
        pictures=row.pictures,
        story_count=story_counts.get(row.id, 0),
    )


class CardSummary(Schema):
    """A card as the codex lists it: its name, aliases and tagline, its
    pictures in order — the first the portrait — and how many stories it
    is in, one linked character in each."""

    id: int
    name: str
    aliases: tuple[str, ...]
    tagline: str
    pictures: tuple[Attachment, ...]
    story_count: int


class Card(Schema):
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


class StoryCharacter(Schema):
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


class CardCheck(Schema):
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


class CharacterLinkCheck(Schema):
    """A link to a card vetted before it is made, nothing stored — a story
    character linked, or promoted to a card of its own: how many copies
    the write takes along when asked to (`with_copies`), in the stories forks
    join the character's own to."""

    copies: int


class CardImport(Schema):
    """What a card file became: the card, and whether it was made now —
    False where the codex held it already, or the reader said which
    card it is."""

    card: Card
    made: bool
