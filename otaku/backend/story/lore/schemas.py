"""What `story.lore.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema
from otaku.store import schema


class Lore(Schema):
    """A story's memory as the browser shows it: how many messages its
    chain holds, how far the extraction has read (`extracted_up_to`, a
    1-based chain position, 0 for nothing yet — what lies past it is no
    fault: the settle margin leaves the newest messages open on
    purpose), its current scenes in story order, the cast in order of
    appearance, and the current timeline's journal records."""

    story_id: int
    message_count: int
    extracted_up_to: int
    scenes: tuple[Scene, ...]
    characters: tuple[Character, ...]
    journals: tuple[Journal, ...]


class Scene(Schema):
    """One scene of a story's memory: its number in story order (0 for
    one not in the memory) and its "N-M" message range on the current
    chain ("" where either end is off it), its title, summary and arc
    (the story so far through it; derived, "" where not generated here),
    the messages it starts and ends at, who made it, and when it was
    last written (for display alone)."""

    id: int
    no: int
    span: str
    title: str
    summary: str
    history: str
    start_message_id: int
    end_message_id: int
    provider: str | None
    model: str | None
    updated_at: str


class ProfileFields(Schema):
    """A story character's profile, field by field — every field a codex
    card has (`codex.cards.Card`), an empty one empty — what its TOML
    (`Character.profile`) is written from, and corrected by.
    `context.cards.CharacterProfile` less `name` and `file_name`: the
    fields a reader writes, so an answer reads what a correction sends
    — the name is the character's own, the file a record — and the HTTP
    shape stays apart from the context class."""

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


class Character(Schema):
    """One character of a story's cast: its name and aliases, its
    description, the profile an imported one carries, as TOML (None for
    one the extractor made) and field by field, its arc so far
    — the newest non-empty rollup, "" before a pass has written one — and
    the id of the journal record it was taken from (where a frontend says
    how old it is), the codex card it is linked to, that card's name and
    its portrait (None for none), whether the card row that brought it in
    on the current timeline carries that picture (None where none did),
    who made it — a card's, the card alone, no model
    named — and when it was last written (for display alone)."""

    id: int
    name: str
    aliases: tuple[str, ...]
    description: str
    profile: str | None
    profile_fields: ProfileFields | None
    history: str
    history_journal_id: int | None
    card_id: int | None
    card_name: str | None
    card_portrait: schema.Attachment | None
    portrait_sent: bool | None
    provider: str | None
    model: str | None
    updated_at: str


class Journal(Schema):
    """One journal record: the scene it belongs to and that scene's
    number, the character it is about, the entry (the author's to
    correct), the state and the arc (the extractor's own), how far the
    story has moved past its scene, in messages, who made it, and when it
    was last written (for display alone)."""

    id: int
    scene_id: int
    scene_no: int
    character_id: int
    entry: str
    state: str
    history: str
    messages_since: int
    provider: str | None
    model: str | None
    updated_at: str


class Extraction(Schema):
    """A story's forced extraction pass as it stands: its `status` —
    "running", "done", "cancelled" or "failed" — and, once it is not
    running, its one-line `report`: scenes closed, journals written,
    too little new play yet, or what failed and why."""

    story_id: int
    status: str
    report: str | None
