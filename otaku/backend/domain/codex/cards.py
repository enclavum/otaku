"""The codex's character cards: a character as the reader keeps it across
stories. A card is made by hand, from a card file — its picture kept, and
a file the codex already holds found again rather than made twice — or
from a story's character, promoted and linked to it; read and listed,
corrected, its pictures added, ordered and removed, merged into another,
deleted. And every story's character no card claims: what a reader
links. A card's fields are `StoryCard`'s, kept as the TOML
`context.cards` writes, its name beside them in a column of its own; what
it shows of the stories it reads through the store, never through
`domain.story`, which stands above it.

A card file is the codex's ALREADY when the digest of the card it holds —
its fields as read, the file's name left out — is among a card's
`digests`: the import makes nothing then and answers that card. A card
of the same name is only offered (`CheckedCardFile.similar_cards`): it is the
same card where the reader says so (`reuse`), which notes the file's
digest on it.
"""

import hashlib
import json
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

from otaku.backend.domain._formats.cards import CardError, load_card
from otaku.backend.domain.app import App
from otaku.backend.domain.app.pictures import RawFile, read_picture, save
from otaku.backend.errors import NO_SUCH_STORY, Conflict, Invalid, NotFound, Refused
from otaku.context.cards import StoryCard, fields_toml, read_fields
from otaku.store.files import Owner
from otaku.store.ops.lore import CastListing
from otaku.store.schema import Card, Character

# The fields a reader writes; the name has a column of its own, and the
# file a card came from is a record, not a field anyone corrects.
FIELDS = tuple(f.name for f in fields(StoryCard) if f.name not in ("name", "file_name"))

_NO_SUCH_CARD = "No such card."
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class StoryCharacter:
    """A story's character as the codex lists it: the story it is in and
    the label a reader finds that story by, the character, and how many
    journal rows it has, on any of the story's timelines."""

    story_id: int
    story_label: str
    character: Character
    journal_count: int


@dataclass(frozen=True)
class CheckedCardFile:
    """A card file read for the codex, nothing stored: the card as it
    would be made (renamed where asked), what reading it noted, whether
    it carries a picture, the card the codex already holds it as (None
    for none), and the cards of the same name the reader may say it is."""

    card: StoryCard
    notes: tuple[str, ...]
    has_picture: bool
    same_card: Card | None
    similar_cards: tuple[Card, ...]


# ---------- the cards ----------


def list_cards(app: App, *, query: str = "") -> tuple[Card, ...]:
    """Every card by name, or those whose name, an alias or the tagline
    holds `query`, cased as the reader likes."""
    needle = query.strip().casefold()
    cards = app._store.cards.list()
    if needle:
        cards = [c for c in cards if needle in " ".join((c.name, *c.aliases, c.tagline)).casefold()]
    return tuple(sorted(cards, key=lambda c: (c.name.casefold(), c.id)))


def get_card(app: App, card_id: int) -> Card:
    """One card. Raises NotFound for a card the codex does not hold."""
    card = app._store.cards.get(card_id)
    if card is None:
        raise NotFound(_NO_SUCH_CARD)
    return card


def story_card_of(card: Card) -> StoryCard:
    """A card's fields as the card they make, under its name."""
    return read_fields(card.fields, name=card.name)


def get_story_counts(app: App) -> dict[int, int]:
    """How many stories each card is in — one linked character in each —
    by the card's id; a card in none is absent."""
    return app._store.characters.get_link_counts()


def list_linked(app: App, card_id: int) -> tuple[StoryCharacter, ...]:
    """The story characters a card is linked to, by story."""
    return _with_labels(app, app._store.characters.list_linked(card_id))


def create_card(
    app: App, *, card: StoryCard, aliases: Sequence[str] = (), tagline: str = ""
) -> tuple[int, str]:
    """A card made by hand from its fields — the new card's id, and the
    sentence. Raises Refused for no name, a tagline of more than one
    line and a reminder's depth below 1."""
    name = _clean_name(card.name)
    card_id = app._store.cards.add(
        name,
        aliases=_clean_aliases(aliases, name),
        tagline=_clean_tagline(tagline),
        fields=fields_toml(_checked(replace(card, name=name))),
    )
    return card_id, f"Added {name} to the codex."


def update_card(
    app: App,
    card_id: int,
    *,
    name: str | None = None,
    aliases: Sequence[str] | None = None,
    tagline: str | None = None,
    changes: Mapping[str, Any] | None = None,
    pictures: Sequence[str] | None = None,
) -> str:
    """The reader's correction of a card — None leaves a part as it is.
    `changes` are fields by name (`FIELDS`); `pictures` each of the
    card's own pictures by name, in the order they now stand, the first
    the portrait (`delete_card_picture` removes one). Raises Refused for
    nothing to change, an emptied name, a tagline of more than one line,
    a field the card has not, a reminder's depth below 1, and an order
    that is not the card's pictures each once."""
    card = get_card(app, card_id)
    if all(part is None for part in (name, aliases, tagline, changes, pictures)):
        raise Invalid("Nothing to change.")
    new_name = _clean_name(name) if name is not None else card.name
    new_fields: str | None = None
    if changes:
        unknown = sorted(set(changes) - set(FIELDS))
        if unknown:
            raise Invalid(f"A card has no {', '.join(unknown)}.")
        new_fields = fields_toml(_checked(replace(story_card_of(card), **changes)))
    ordered = None
    if pictures is not None:
        by_name = {picture.file: picture for picture in card.pictures}
        if sorted(pictures) != sorted(by_name):
            raise Invalid("A new order names each of the card's pictures once.")
        ordered = [by_name[file] for file in pictures]
    if aliases is None and name is not None:
        aliases = card.aliases  # a new name never stays among the aliases
    app._store.cards.update(
        card_id,
        name=new_name if name is not None else None,
        aliases=_clean_aliases(aliases, new_name) if aliases is not None else None,
        tagline=_clean_tagline(tagline) if tagline is not None else None,
        fields=new_fields,
    )
    if ordered is not None:
        app._store.cards.set_pictures(card_id, ordered)
    return "Saved."


def delete_card(app: App, card_id: int) -> str:
    """A card removed from the codex — its pictures with it, its story
    characters unlinked and otherwise untouched — and the sentence."""
    card = get_card(app, card_id)
    linked = len({row.story_id for row in app._store.characters.list_linked(card_id)})
    app._store.cards.delete(card_id)
    app._store.sweep_files()
    said = f"Deleted {card.name} from the codex."
    if linked:
        said += (
            f" Its characters in {linked} {'story' if linked == 1 else 'stories'} stay, unlinked."
        )
    return said


def merge_cards(app: App, card_id: int, *, into: int) -> str:
    """Fold a card into another — its story characters follow, its name
    becomes an alias of `into`, its pictures follow `into`'s — and the
    sentence. Refused where one story holds characters linked to both:
    one card has one character in a story, so they are merged there
    first."""
    source, target = get_card(app, card_id), get_card(app, into)
    if source.id == target.id:
        raise Invalid(f"{source.name} and {target.name} are already the same card.")
    stories = {row.story_id for row in app._store.characters.list_linked(source.id)}
    shared = [row for row in list_linked(app, target.id) if row.story_id in stories]
    if shared:
        raise Conflict(
            f'"{shared[0].story_label}" has characters linked to both {source.name} and '
            f"{target.name} — merge them in the story first."
        )
    app._store.cards.merge(source.id, target.id)
    return f"Merged {source.name} into {target.name} ('{source.name}' is now an alias)."


# ---------- a card's pictures ----------


def create_card_picture(app: App, card_id: int, file: RawFile) -> str:
    """A picture added to a card, after its others — read through the one
    intake, stored under the card's number — and the sentence. Raises
    Refused for a picture the intake refuses, and one the card already
    has."""
    card = get_card(app, card_id)
    attachment = save(app._store.files, read_picture(file), Owner.CARD, card_id)
    if any(picture.file == attachment.file for picture in card.pictures):
        raise Conflict(f"{card.name} has this picture already.")
    app._store.cards.set_pictures(card_id, [*card.pictures, attachment])
    return "Picture added."


def delete_card_picture(app: App, card_id: int, name: str) -> str:
    """A picture taken off a card by its file name — the file going once
    nothing names it, so a story's card row it rode keeps it — and the
    sentence. Raises NotFound for a picture that is not the card's."""
    card = get_card(app, card_id)
    kept = [picture for picture in card.pictures if picture.file != name]
    if len(kept) == len(card.pictures):
        raise NotFound(f"{card.name} has no such picture.")
    app._store.cards.set_pictures(card_id, kept)
    app._store.sweep_files()
    return "Picture removed."


# ---------- a card file ----------


def check_card_file(app: App, data: bytes, file_name: str, *, rename: str = "") -> CheckedCardFile:
    """A card file read for the codex, nothing stored: the card it holds
    and what reading it noted, whether it carries a picture, and the
    cards the codex may already hold it as. Raises Refused for a file
    that is no card."""
    card, notes = _read(data, file_name)
    same = app._store.cards.find_by_digest(_digest(card))
    named = replace(card, name=_clean_name(rename)) if rename.strip() else card
    wanted = {named.name.casefold(), card.name.casefold()}
    similar = tuple(
        c
        for c in list_cards(app)
        if (same is None or c.id != same.id)
        and wanted & {c.name.casefold(), *(alias.casefold() for alias in c.aliases)}
    )
    return CheckedCardFile(
        card=named,
        notes=tuple(notes),
        has_picture=data.startswith(_PNG_MAGIC),
        same_card=same,
        similar_cards=similar,
    )


def import_card_file(
    app: App, data: bytes, file_name: str, *, rename: str = "", reuse: int | None = None
) -> tuple[int, bool, str]:
    """A card file into the codex — the card it is there, whether it was
    made now, and the sentence. A file the codex holds already makes
    nothing; `reuse` names the card the reader says it is, which notes
    the file's digest. Otherwise a card is made — renamed where asked —
    and a PNG's picture kept as its portrait; a picture the intake
    refuses leaves the card without one, and the sentence says so.
    Raises Refused for a file that is no card, and a `reuse` the codex
    does not hold."""
    card, notes = _read(data, file_name)
    digest = _digest(card)
    same = app._store.cards.find_by_digest(digest)
    if same is not None:
        return same.id, False, f"{same.name} is in the codex already."
    if reuse is not None:
        kept = get_card(app, reuse)
        app._store.cards.add_digest(kept.id, digest)
        return kept.id, False, f"Kept {kept.name}: the codex holds it already."
    name = _clean_name(rename) if rename.strip() else card.name
    card_id = app._store.cards.add(name, fields=fields_toml(card), digests=(digest,))
    said = [f"Added {name} to the codex", *notes]
    if data.startswith(_PNG_MAGIC):
        try:
            picture = read_picture(RawFile(data, file_name))
        except Refused as e:
            said.append(f"its picture was not kept: {e}")
        else:
            app._store.cards.set_pictures(
                card_id, [save(app._store.files, picture, Owner.CARD, card_id)]
            )
    return card_id, True, " · ".join(said) + "."


# ---------- a story's characters ----------


def promote_character(app: App, story_id: int, character_id: int) -> tuple[int, str]:
    """A card made from a story's character and linked to it — its name
    and aliases, the fields of the card it was imported from (the
    `{{user}}` binding left behind), else its description — the new
    card's id, and the sentence. Raises NotFound for a story or a
    character that is not there, Conflict for one linked already."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    character = next(
        (c for c in app._store.characters.list(story_id) if c.id == character_id), None
    )
    if character is None:
        raise NotFound("That character is not in this story's memory.")
    if character.card_id is not None:
        linked = get_card(app, character.card_id)
        raise Conflict(f"This character is linked to {linked.name} in the codex already.")
    card = StoryCard(name=character.name)
    if character.card:
        # An archive edited past reading leaves the description to stand in.
        with suppress(tomllib.TOMLDecodeError):
            card = read_fields(character.card, name=character.name)
    if not card.description:
        card = replace(card, description=character.description)
    card_id = app._store.cards.add(
        character.name,
        aliases=_clean_aliases(character.aliases, character.name),
        fields=fields_toml(card),
    )
    app._store.characters.set_card_id(character.id, card_id)
    return card_id, f"Added {character.name} to the codex, linked to this story's character."


def list_unlinked(app: App, *, query: str = "") -> tuple[StoryCharacter, ...]:
    """Every story's character no card claims — or those whose name, an
    alias or a journal record (its entry or its state, on any of the
    story's timelines) holds `query`, cased as the reader likes — the
    most written about first: by journal rows, then story, then
    appearance."""
    needle = query.strip().casefold()
    rows = app._store.characters.list_unlinked()
    if needle:
        journaled = _journal_texts(app, {r.story_id for r in rows})
        rows = [
            r
            for r in rows
            if needle in " ".join((r.character.name, *r.character.aliases)).casefold()
            or needle in journaled.get(r.character.id, "")
        ]
    return tuple(sorted(_with_labels(app, rows), key=lambda r: -r.journal_count))


# ---------- helpers ----------


def _read(data: bytes, file_name: str) -> tuple[StoryCard, list[str]]:
    """The card a file holds and what reading it noted; Refused for a file
    that is no card."""
    try:
        return load_card(data, file_name=file_name)
    except CardError as e:
        raise Invalid(f"Not a character card: {e}") from e


def _digest(card: StoryCard) -> str:
    """What tells one card file's card from another's: its fields as read,
    the file's name left out — the same card saved as PNG or as JSON, or
    under another name, is one card."""
    text = json.dumps(asdict(replace(card, file_name="")), sort_keys=True, ensure_ascii=False)
    return hashlib.blake2b(text.encode(), digest_size=16).hexdigest()


def _journal_texts(app: App, story_ids: Iterable[int]) -> dict[int, str]:
    """Each character's journal records as one text, casefolded, by the
    character's id — their entries and states, what a reader reads of
    them."""
    parts: dict[int, list[str]] = {}
    for story_id in story_ids:
        for record in app._store.journals.list(story_id):
            parts.setdefault(record.character_id, []).extend((record.entry, record.state))
    return {character_id: "\n".join(texts).casefold() for character_id, texts in parts.items()}


def _with_labels(app: App, rows: Sequence[CastListing]) -> tuple[StoryCharacter, ...]:
    """The rows with the label each one's story is found by."""
    labels = {listing.id: listing.label for listing in app._store.stories.list()} if rows else {}
    return tuple(
        StoryCharacter(row.story_id, labels.get(row.story_id, ""), row.character, row.journal_count)
        for row in rows
    )


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise Invalid("A card needs a name.")
    return cleaned


def _checked(card: StoryCard) -> StoryCard:
    """A card's fields as the reader wrote them, refused where a number
    is out of its range: a reminder goes before one of the reader's
    messages, the newest 1."""
    if type(card.reminder_depth) is not int or card.reminder_depth < 1:
        raise Invalid("A reminder's depth is a whole number from 1.")
    return card


def _clean_tagline(tagline: str) -> str:
    cleaned = tagline.strip()
    if "\n" in cleaned:
        raise Invalid("A tagline is one line.")
    return cleaned


def _clean_aliases(aliases: Sequence[str], name: str) -> tuple[str, ...]:
    """The aliases trimmed, the empty and the name itself left out, each
    once."""
    cleaned = (alias.strip() for alias in aliases)
    return tuple(dict.fromkeys(a for a in cleaned if a and a.casefold() != name.casefold()))
