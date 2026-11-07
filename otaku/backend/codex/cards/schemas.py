"""What `codex.cards.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.app.pictures.services import RawFile as RawFile
from otaku.backend.base import Schema
from otaku.backend.codex.cards import services as domain_cards
from otaku.store import schema
from otaku.store.pictures import CardPicture


def as_card_summary(row: schema.Card, story_counts: dict[int, int]) -> CardSummary:
    return CardSummary(
        id=row.id,
        name=row.name,
        aliases=row.aliases,
        tagline=row.tagline,
        pictures=row.portraits,
        story_count=story_counts.get(row.id, 0),
        is_blank=domain_cards.is_blank(domain_cards.card_profile(row)),
    )


class CardSummary(Schema):
    """A card as the codex lists it: its name, aliases and tagline, its
    pictures in order — the first the portrait — how many stories it is
    in, one linked character in each, and whether it holds no text yet,
    which no story takes (`is_blank`)."""

    id: int
    name: str
    aliases: tuple[str, ...]
    tagline: str
    pictures: tuple[CardPicture, ...]
    story_count: int
    is_blank: bool


class Card(Schema):
    """One card whole: its name, aliases and tagline (one line for the
    reader, never sent to the model); the card's fields, macros as
    written, and the file it came from ("" for none); its pictures in
    order, the first the portrait; the story characters it is linked to;
    whether it holds no text yet, which no story takes (`is_blank`); what
    it can be exported as (`available_export_formats`, of
    `EXPORT_FORMATS`), what it is unless another is asked
    (`default_export_format`), and the kind of file it came as where it
    keeps it (`original_format`, None for none); and when it was last
    written (for display alone)."""

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
    pictures: tuple[CardPicture, ...]
    stories: tuple[StoryCharacter, ...]
    is_blank: bool
    default_export_format: str
    available_export_formats: tuple[str, ...]
    original_format: str | None
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
    the card would take, what the import should say out loud, each a
    sentence, whether the file carries a picture and that picture's
    thumbnail, a JPEG (None for none), the card the codex holds it as
    already (None for none: an import makes a card), the cards alike —
    the same name, or the same picture — the reader may say it is, and
    what the import says of them, a sentence ("" for none)."""

    name: str
    notes: tuple[str, ...]
    has_picture: bool
    thumbnail: bytes | None
    same_card: CardSummary | None
    similar_cards: tuple[CardSummary, ...]
    similar_note: str


class CardFetchCheck(CardCheck):
    """A card read from a link and vetted as a file is (`CardCheck`),
    nothing stored, and the file the link gave — its name and bytes —
    with the line its site shows it under ("" for none), for the import
    to send as a file, so the link is fetched once."""

    file_name: str
    data: bytes
    tagline: str


class CharacterLinkCheck(Schema):
    """A link to a card vetted before it is made, nothing stored — a story
    character linked, or promoted to a card of its own: how many copies
    the write takes along when asked to (`with_copies`), in the stories forks
    join the character's own to."""

    copies: int


class CardExport(Schema):
    """A card as a file, for a frontend to save: the name it is saved
    under, its bytes, and their media type."""

    name: str
    data: bytes
    media_type: str


class CardImport(Schema):
    """What a card file became: the card, and whether it was made now —
    False where the codex held it already, or the reader said which
    card it is."""

    card: Card
    made: bool
