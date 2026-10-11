"""A character card into a story, from its FILE's bytes: read and vetted
for the story, nothing stored (`check_card`), then imported
(`import_card`) — the typed /card row, the character with the card's
archive, the greeting turn: three store writes and NO model call.

The `/card` line lands as a `kind='card'` user turn, stored AS TYPED like
every direction — the wire composes the card block from the archive at
request time, found through the row's speaker link, so a lore correction
to the TOML reaches every future request. The greeting lands as an
AUTHORED assistant turn, speaker set, provider `card` and the file's name
for a model — the source, spelled the way generated rows spell theirs:
the card introduces the character and the character answers, so the
model continues the voice."""

import tomllib
from contextlib import suppress
from dataclasses import dataclass, replace

from otaku.backend.domain._formats.cards import CardError, load_card
from otaku.backend.domain.app import App
from otaku.backend.domain.story import _chain
from otaku.backend.domain.story.settings import settings_of
from otaku.backend.errors import Conflict, Invalid
from otaku.context.assembler import estimate_tokens
from otaku.context.cards import Card, bind, card_to_wire, card_toml
from otaku.store.schema import Message

# A card block above roughly this many tokens gets a warning: it rides
# every request verbatim and never leaves the context, so on a small
# local window it crowds out the story itself.
_LARGE_CARD_TOKENS = 4000


@dataclass(frozen=True)
class CheckedCard:
    """A card read and vetted for a story: the card as it would be
    imported, what reading it noted, the composed block's cost estimate
    and whether it is worth warning about, and the persona the story
    remembers ("" for none)."""

    card: Card
    notes: tuple[str, ...]
    block_tokens: int
    large: bool
    persona: str


def check_card(
    app: App, story_id: int, data: bytes, file_name: str, *, rename: str = ""
) -> CheckedCard:
    """The card the bytes hold, vetted for the story and nothing stored;
    `rename` overrides its own name. Raises Refused — not a card, or a
    cast name already taken."""
    card, notes = _read_card(app, story_id, data, file_name, rename)
    remembered = _remembered_persona(app, story_id)
    tokens = _block_tokens(app, card, remembered)
    return CheckedCard(
        card=card,
        notes=tuple(notes),
        block_tokens=tokens,
        large=tokens > _LARGE_CARD_TOKENS,
        persona=remembered,
    )


def import_card(
    app: App,
    story_id: int,
    data: bytes,
    file_name: str,
    *,
    rename: str = "",
    persona: str,
    line: str = "",
    expected_head: int | None,
) -> tuple[Message, Message | None, str]:
    """The card imported into the story, played on from `expected_head`,
    the head the caller read (HeadMoved where the head stands
    elsewhere): the typed row (speaker-linked to the new cast row), the
    archive on the character, the greeting turn — answered as the two
    rows (the greeting None where the card has none) and the one-line
    report. The same bytes are vetted again. `/undo` takes the exchange
    whole — the unplayed character with it. `line` is what the story
    RECORDS; an upload has only a file name, which is what the default
    composes."""
    card, notes = _read_card(app, story_id, data, file_name, rename)
    tokens = _block_tokens(app, card, persona)
    row_line = line or (f"/card {file_name}" + (f" {rename}" if rename else ""))
    toml = card_toml(card, user=persona)
    mode = settings_of(app, story_id).layout.mode
    row = _chain.append_message(
        app,
        story_id,
        Message(role="user", body=row_line, kind="card", mode=mode),
        after=expected_head,
    )
    # Made by the card, spelled as the greeting spells its source below.
    character_id = app._store.characters.add(
        story_id, card.name, card=toml, provider="card", model=card.file_name
    )
    # The speaker link is the compose key: the row was recorded before
    # the cast row could exist, so the attribution follows it.
    app._store.messages.set_speaker(row.id, character_id, card.name)
    recorded_line = replace(row, speaker=card.name, speaker_id=character_id)
    greeting_row: Message | None = None
    greeting = bind(card.greeting, char=card.name, user=persona)
    if greeting:
        greeting_row = _chain.append_message(
            app,
            story_id,
            Message(
                role="assistant",
                body=greeting,
                kind="dialogue",
                speaker=card.name,
                speaker_id=character_id,
                # The source, spelled the way generated rows spell theirs:
                # no model wrote this — the card's file did.
                provider="card",
                model=card.file_name,
                mode=mode,
            ),
            after=row.id,
        )
    report = [f"imported {card.name} (card ≈ {tokens} tokens)", *notes]
    if tokens > _LARGE_CARD_TOKENS:
        report.append("a large card — it rides every request and never leaves the context")
    return recorded_line, greeting_row, " · ".join(report)


def _read_card(
    app: App, story_id: int | None, data: bytes, file_name: str, rename: str
) -> tuple[Card, list[str]]:
    """The card the bytes hold, renamed where asked, and what reading it
    noted — refused when it is no card, or the story's cast already
    answers to its name: importing over it would double the character."""
    try:
        card, notes = load_card(data, file_name=file_name)
    except CardError as e:
        raise Invalid(f"Not a character card: {e}") from e
    if rename:
        card = replace(card, name=rename)
    if story_id is not None:
        existing = app._store.characters.find(story_id, card.name)
        if existing is not None:
            source = "imported earlier" if existing.card else "extracted from play"
            raise Conflict(
                f"{existing.name} already exists in this story ({source}) — "
                f"import under another name: /card FILE NAME."
            )
    return card, notes


def _block_tokens(app: App, card: Card, persona: str) -> int:
    """The composed block's cost estimate, over the persona the ask
    defaults to — the answer only swaps a name, never the size class."""
    return estimate_tokens(
        card_to_wire(card_toml(card, user=persona or "you"), app._prompts().card_framing)
    )


def _remembered_persona(app: App, story_id: int | None) -> str:
    """A story's memory of who the player is: the `user` binding in the
    latest imported card's archive — so a story asks once and later
    imports default to the same name, with no setting anywhere. "" when
    none, or no story — the frontend asks with its own default then."""
    if story_id is None:
        return ""
    for character in reversed(app._store.characters.list(story_id)):
        if not character.card:
            continue
        with suppress(tomllib.TOMLDecodeError):
            user = tomllib.loads(character.card).get("user")
            if isinstance(user, str) and user:
                return user
    return ""
