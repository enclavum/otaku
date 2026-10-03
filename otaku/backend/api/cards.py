"""The /card operation, split where the persona ask sits: `prepare`
does everything before the question (read, decode, refuse duplicates),
the frontend asks it, `add` does everything after — three store
writes and NO model call. The card line lands as a typed row whose wire
block composes from the archive at request time; the greeting plays as
the character's own first words.
"""

import tomllib
from contextlib import suppress
from dataclasses import dataclass, replace

from otaku.backend.formats.cards import CardError, load_card
from otaku.backend.session import Refused, Session
from otaku.context.assembler import estimate_tokens
from otaku.context.cards import Card, bind, card_to_wire, card_toml
from otaku.store.schema import Message

# A card block above roughly this many tokens gets a warning: it rides
# every request verbatim and never leaves the context, so on a small
# local window it crowds out the story itself.
_LARGE_CARD_TOKENS = 4000


@dataclass(frozen=True)
class PreparedCard:
    """A card read and vetted, waiting on the persona answer. `card` is
    an OPAQUE token between `prepare` and `add`: frontends hold it and
    hand it back, never read into it — the ask needs only
    `remembered_persona`."""

    card: Card  # renamed already, when the argument renamed it
    line: str  # the /card row's body — file name + the rename as given
    notes: tuple[str, ...]  # what the import should say out loud
    block_tokens: int  # the composed block's cost estimate
    large: bool  # worth warning: it rides every request verbatim


@dataclass(frozen=True)
class ImportedCard:
    """What landed: the recorded /card row, the greeting turn (None when
    the card had none), and the one-line report."""

    line: Message
    greeting: Message | None
    report: str


def prepare(
    session: Session, data: bytes, file_name: str, rename: str = "", *, line: str = ""
) -> PreparedCard:
    """A card FILE's bytes, vetted and normalized; `rename` overrides the
    card's own name. Bytes-shaped on purpose: resolving a path (the
    terminal's `FILE [NAME]` split, the `@` strip) is the frontend's —
    the web uploads. `line` is what the story RECORDS: the terminal
    passes the argument as typed, so the row reads back like every
    other command's; an upload has only a file name, which is what the
    default composes. Raises Refused — not a card, or a cast name
    already taken."""
    try:
        card, notes = load_card(data, file_name=file_name)
    except CardError as e:
        raise Refused(f"Not a character card: {e}") from e
    if rename:
        card = replace(card, name=rename)
    if session.story_id is not None:
        existing = session._store.characters.find(session.story_id, card.name)
        if existing is not None:
            # A refusal, not a failure: the cast already answers to this
            # name — importing over it would double the character.
            source = "imported earlier" if existing.card else "extracted from play"
            raise Refused(
                f"{existing.name} already exists in this story ({source}) — "
                f"import under another name: /card FILE NAME."
            )
    # The cost estimate composes with the persona the ask will DEFAULT
    # to — the answer only swaps a name, never the size class.
    block = card_to_wire(
        card_toml(card, user=remembered_persona(session) or "you"),
        session._prompts.card_framing,
    )
    tokens = estimate_tokens(block)
    row = line or (f"/card {file_name}" + (f" {rename}" if rename else ""))
    return PreparedCard(
        card=card,
        line=row,
        notes=tuple(notes),
        block_tokens=tokens,
        large=tokens > _LARGE_CARD_TOKENS,
    )


def remembered_persona(session: Session) -> str:
    """The story's memory of who the player is: the `user` binding in the
    latest imported card's archive — so a story asks once and later
    imports default to the same name, with no setting anywhere. "" when
    none — the frontend asks with its own default then."""
    if session.story_id is None:
        return ""
    for character in reversed(session._store.characters.list(session.story_id)):
        if not character.card:
            continue
        with suppress(tomllib.TOMLDecodeError):
            user = tomllib.loads(character.card).get("user")
            if isinstance(user, str) and user:
                return user
    return ""


def add(session: Session, prepared: PreparedCard, persona: str) -> ImportedCard:
    """The import itself: the typed row (speaker-linked to the new cast
    row), the archive on the character, the greeting turn. `/undo` takes
    the exchange whole — the unplayed character with it.

    The `/card` line lands as a `kind='card'` user turn, stored AS TYPED
    like every direction — the wire composes the card block from the
    archive at request time, found through the row's speaker link, so a
    lore correction to the TOML reaches every future request. The
    greeting lands as an AUTHORED assistant turn, speaker set, provider
    `card` and the file's name for a model — the source, spelled the way
    generated rows spell theirs: the card introduces the character and
    the character answers, so the model continues the voice."""
    card = prepared.card
    toml = card_toml(card, user=persona)
    session._record_turn(
        Message(role="user", body=prepared.line, kind="card", mode=session.layout.mode)
    )
    story_id = session.story_id
    assert story_id is not None  # _record_turn just ensured it
    # Made by the card, spelled as the greeting spells its source below.
    character_id = session._store.characters.add(
        story_id, card.name, card=toml, provider="card", model=card.file_name
    )
    # The speaker link is the compose key: the row was recorded before
    # the cast row could exist, so the attribution follows it.
    row = session._messages[-1]
    session._store.messages.set_speaker(row.id, character_id, card.name)
    session._messages[-1] = replace(row, speaker=card.name, speaker_id=character_id)
    recorded_line = session._messages[-1]
    greeting_row: Message | None = None
    greeting = bind(card.greeting, char=card.name, user=persona)
    if greeting:
        session._record_turn(
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
                mode=session.layout.mode,
            )
        )
        greeting_row = session._messages[-1]
    report = [f"imported {card.name} (card ≈ {prepared.block_tokens} tokens)", *prepared.notes]
    if prepared.large:
        report.append("a large card — it rides every request and never leaves the context")
    return ImportedCard(line=recorded_line, greeting=greeting_row, report=" · ".join(report))


def drop_unplayed_card(session: Session, popped: list[Message]) -> None:
    """Undoing a card import takes the character with it — while they are
    UNPLAYED: the import wrote the row, the undo unwrites it, and the
    duplicate refusal stops blocking a retry (the reason imports get
    undone at all). A journal marks them played — extraction wove them
    into the story — and then the cast row stays: a character with
    history is story, not import residue."""
    story_id = session.story_id
    assert story_id is not None  # undo just popped from this story
    for row in popped:
        if row.kind != "card" or row.speaker_id is None:
            continue
        journaled = any(
            j.character_id == row.speaker_id for j in session._store.journals.list(story_id)
        )
        if not journaled:
            session._store.characters.delete(row.speaker_id)
