"""The codex's character cards: a character as the reader keeps it across
stories. A card is made by hand, from a card file — its picture kept, and
a file the codex already holds found again rather than made twice — or
from a story's character, promoted and linked to it — its copies in the
stories forks join its own to with it, where the reader says so; read and listed
in the reader's own order — a card made goes last — corrected, moved in
that order, its pictures added, ordered and removed, merged into
another, deleted. And every story's character no card claims: what a reader
links. A card's fields are its `CharacterProfile`, kept as the TOML
`context.cards` writes, its name beside them in a column of its own; what
it shows of the stories it reads through the store, never through
the story's services, which stand above it.

A picture that holds no card comes into the codex as a blank card, the
picture its portrait — the codex's door alone, never a story's — and a
card's pictures are where the model in use writes it from: its tagline,
description, personality, scenario and greeting, and its name while it
is still `NEW_CARD_NAME` (`generate_card`).

A card file is the codex's ALREADY when the digest of the card it holds —
its fields as read, the file's name left out — is among a card's
`digests`: the import makes nothing then and answers that card. A card
of the same name is only offered (`CheckedCardFile.similar_cards`): it is the
same card where the reader says so (`reuse`), which notes the file's
digest on it.
"""

import hashlib
import json
import threading
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

from otaku.backend.app.core.services import App
from otaku.backend.app.pictures.services import RawFile, read_picture, save
from otaku.backend.errors import (
    NO_MODEL_HINT,
    NO_SUCH_STORY,
    Conflict,
    Invalid,
    NotFound,
    Refused,
    Unavailable,
)
from otaku.backend.formats.cards import CardError, has_embedded_cards, load_card
from otaku.context.cards import CharacterProfile
from otaku.context.prompt import WirePicture, WireTurn
from otaku.formatting import parse_json_object, render
from otaku.providers import OpenAIClient, PicturesRide, ProviderError, Stats, Text
from otaku.settings.state import ModelRef, ModelRole
from otaku.store.files import Owner
from otaku.store.ops.lore import CastListing
from otaku.store.schema import Card, Character

# The fields a reader writes; the name has a column of its own, the
# `user` a story binds is the story's, and the file a card came from is a
# record, not a field anyone corrects.
FIELDS = tuple(
    f.name for f in fields(CharacterProfile) if f.name not in ("name", "user", "file_name")
)

# What a card made by hand is called where no name is given: the page
# makes one so, and opens it on its name for the reader to type over.
NEW_CARD_NAME = "New card"

_NO_SUCH_CARD = "No such card."
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_UTF8_BOM = b"\xef\xbb\xbf"

# What a generation writes, besides the name: the card's text as the
# model reads a picture into it.
_GENERATED = ("tagline", "description", "personality", "scenario", "greeting")
# How a generation is asked: warm enough to invent what a picture leaves
# open, and bounded, as the lore pass is, against a looping model.
_GENERATION_PARAMS: dict[str, object] = {"temperature": 0.7, "max_tokens": 2000}
_CANNOT_SEE = "The model in use cannot see pictures."
_NO_PICTURE = "The card has no picture."
_NO_GENERATION = "No generation is running."


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
    """A card file read for the codex, nothing stored: the profile the
    card would be made of (renamed where asked), what reading it noted,
    whether it carries a picture, the card the codex already holds it as
    (None for none), and the cards of the same name the reader may say
    it is."""

    profile: CharacterProfile
    notes: tuple[str, ...]
    has_picture: bool
    same_card: Card | None
    similar_cards: tuple[Card, ...]


# ---------- the cards ----------


def list_cards(app: App, *, query: str = "") -> tuple[Card, ...]:
    """Every card in the reader's order, or those whose name, an alias or
    the tagline holds `query`, cased as the reader likes."""
    needle = query.strip().casefold()
    cards = app._store.cards.list()
    if needle:
        cards = [c for c in cards if needle in " ".join((c.name, *c.aliases, c.tagline)).casefold()]
    return tuple(cards)


def get_card(app: App, card_id: int) -> Card:
    """One card. Raises NotFound for a card the codex does not hold."""
    card = app._store.cards.get(card_id)
    if card is None:
        raise NotFound(_NO_SUCH_CARD)
    return card


def get_story_counts(app: App) -> dict[int, int]:
    """How many stories each card is in — one linked character in each —
    by the card's id; a card in none is absent."""
    return app._store.characters.get_link_counts()


def list_linked(app: App, card_id: int) -> tuple[StoryCharacter, ...]:
    """The story characters a card is linked to, by story."""
    return _with_labels(app, app._store.characters.list_linked(card_id))


def create_card(
    app: App, *, profile: CharacterProfile, aliases: Sequence[str] = (), tagline: str = ""
) -> tuple[int, str]:
    """A card made by hand from its profile — the new card's id, and the
    sentence. Raises Refused for no name, a tagline of more than one
    line and a reminder's depth below 1."""
    name = _clean_name(profile.name)
    card_id = app._store.cards.add(
        name,
        aliases=_clean_aliases(aliases, name),
        tagline=_clean_tagline(tagline),
        profile=_checked_profile(replace(profile, name=name)).to_toml(),
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
    new_profile: str | None = None
    if changes:
        new_profile = corrected_profile(card_profile(card), changes).to_toml()
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
        profile=new_profile,
    )
    if ordered is not None:
        app._store.cards.set_pictures(card_id, ordered)
    return "Saved."


def move_card(app: App, card_id: int, *, before: int | None) -> str:
    """A card put in the reader's order just before the card `before`, or
    last for None, the others keeping theirs — and the sentence. Refused
    as `check_move` refuses."""
    card = check_move(app, card_id, before=before)
    order = [each.id for each in app._store.cards.list() if each.id != card_id]
    order.insert(order.index(before) if before is not None else len(order), card_id)
    app._store.cards.set_order(order)
    return f"Moved {card.name}."


def check_move(app: App, card_id: int, *, before: int | None) -> Card:
    """The card a move would put just before `before` (None: last),
    nothing written — so a write that also corrects it is refused whole.
    Raises NotFound for either card not in the codex, and Refused for a
    card put before itself."""
    card = get_card(app, card_id)
    if before == card_id:
        raise Invalid("A card cannot go before itself.")
    if before is not None:
        get_card(app, before)
    return card


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
            f"{target.name}."
        )
    app._store.cards.merge(source.id, target.id)
    return f"Merged {source.name} into {target.name} ('{source.name}' is now an alias)."


# ---------- profiles ----------


def card_profile(card: Card) -> CharacterProfile:
    """A card's profile, under its name."""
    return CharacterProfile.from_toml(card.profile, name=card.name)


def story_profile(character: Character) -> CharacterProfile | None:
    """A story character's profile — its copy of its card's, under the
    name it came in as, and the `user` it binds; None for a character no
    card brought, or one whose profile is no TOML."""
    if not character.profile:
        return None
    try:
        return CharacterProfile.from_toml(character.profile, name=character.name)
    except tomllib.TOMLDecodeError:
        return None


def corrected_profile(profile: CharacterProfile, changes: Mapping[str, Any]) -> CharacterProfile:
    """A profile with the reader's corrections laid over it, by field name
    (`FIELDS`) — a codex card's, or a story's copy of one. Raises Refused
    for a field a card has not and a reminder's depth below 1."""
    unknown = sorted(set(changes) - set(FIELDS))
    if unknown:
        raise Invalid(f"A card has no {', '.join(unknown)}.")
    return _checked_profile(replace(profile, **changes))


def _checked_profile(profile: CharacterProfile) -> CharacterProfile:
    """A profile as the reader wrote it — a codex card's, or a story's
    copy of one — refused where a number is out of its range: a
    reminder goes before one of the reader's messages, the newest 1."""
    if type(profile.reminder_depth) is not int or profile.reminder_depth < 1:
        raise Invalid("A reminder's depth is a whole number from 1.")
    return profile


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


def check_card_file(
    app: App,
    data: bytes,
    file_name: str,
    *,
    rename: str = "",
    allow_bare_picture: bool = False,
) -> CheckedCardFile:
    """A card file read for the codex, nothing stored: the card it holds
    and what reading it noted, whether it carries a picture, and the
    cards the codex may already hold it as — or, with
    `allow_bare_picture`, a picture that holds no card as the blank card
    it would make, the picture vetted by the one intake. Raises Refused
    for a file that is no card, nor a picture where one is allowed."""
    if allow_bare_picture and _is_bare_picture(data):
        read_picture(RawFile(data, file_name))
        return CheckedCardFile(
            profile=CharacterProfile(name=_clean_name(rename) if rename.strip() else NEW_CARD_NAME),
            notes=("no card in it: a blank card, the picture its portrait",),
            has_picture=True,
            same_card=None,
            similar_cards=(),
        )
    profile, notes = _read(data, file_name)
    same = app._store.cards.find_by_digest(_digest(profile))
    named = replace(profile, name=_clean_name(rename)) if rename.strip() else profile
    wanted = {named.name.casefold(), profile.name.casefold()}
    similar = tuple(
        c
        for c in list_cards(app)
        if (same is None or c.id != same.id)
        and wanted & {c.name.casefold(), *(alias.casefold() for alias in c.aliases)}
    )
    return CheckedCardFile(
        profile=named,
        notes=tuple(notes),
        has_picture=data.startswith(_PNG_MAGIC),
        same_card=same,
        similar_cards=similar,
    )


def import_card_file(
    app: App,
    data: bytes,
    file_name: str,
    *,
    rename: str = "",
    reuse: int | None = None,
    allow_bare_picture: bool = False,
) -> tuple[int, bool, str]:
    """A card file into the codex — the card it is there, whether it was
    made now, and the sentence. A file the codex holds already makes
    nothing; `reuse` names the card the reader says it is, which notes
    the file's digest. Otherwise a card is made — renamed where asked —
    and a PNG's picture kept as its portrait; a picture the intake
    refuses leaves the card without one, and the sentence says so. With
    `allow_bare_picture`, a picture that holds no card makes a blank card
    — named as asked, else `NEW_CARD_NAME` — the picture its portrait.
    Raises Refused for a file that is no card, nor a picture where one is
    allowed, and a `reuse` the codex does not hold."""
    if allow_bare_picture and _is_bare_picture(data):
        picture = read_picture(RawFile(data, file_name))
        name = _clean_name(rename) if rename.strip() else NEW_CARD_NAME
        card_id, _ = create_card(app, profile=CharacterProfile(name=name))
        app._store.cards.set_pictures(
            card_id, [save(app._store.files, picture, Owner.CARD, card_id)]
        )
        return card_id, True, f"Added {name} to the codex, the picture its portrait."
    profile, notes = _read(data, file_name)
    digest = _digest(profile)
    same = app._store.cards.find_by_digest(digest)
    if same is not None:
        return same.id, False, f"{same.name} is in the codex already."
    if reuse is not None:
        kept = get_card(app, reuse)
        app._store.cards.add_digest(kept.id, digest)
        return kept.id, False, f"Kept {kept.name}: the codex holds it already."
    name = _clean_name(rename) if rename.strip() else profile.name
    card_id = app._store.cards.add(name, profile=profile.to_toml(), digests=(digest,))
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


def _is_bare_picture(data: bytes) -> bool:
    """Whether a file is a picture that holds no card: a PNG without one,
    or anything that is no JSON object, a byte-order mark before it or
    not. What it is past that, the intake says."""
    if data.startswith(_PNG_MAGIC):
        return not has_embedded_cards(data)
    return not data.removeprefix(_UTF8_BOM).lstrip().startswith(b"{")


# ---------- a card written from its pictures ----------


@dataclass(frozen=True)
class _Generation:
    """What a card's generation asks with, read on the app's thread: the
    model in use and its client, the request — the prompt and every
    picture of the card — how many pictures ride it, and what a cancel
    sets."""

    model: ModelRef
    client: OpenAIClient
    request: WireTurn
    picture_count: int
    cancel: threading.Event


def generate_card(app: App, card_id: int) -> str:
    """A card written by the model in use from its pictures — every one,
    in order — and the prompt (`card_from_picture_prompt`): its tagline,
    description, personality, scenario and greeting replaced, and its
    name while it is still `NEW_CARD_NAME`; the sentence. Safe from any
    thread, and slow on purpose: the card is read and written on the
    app's thread (`App.run`), the model asked off it — until
    `cancel_generation`, which cuts the ask and writes nothing. Raises
    NotFound for a card the codex does not hold; Conflict while no model
    is in use, the model cannot see pictures, the card has none or is
    being written already, and for a generation cancelled; Unavailable
    for what the engine refuses and an answer that is no card."""
    asked = app.run(lambda: _generation(app, card_id))
    try:
        text, stats = _generated(asked)
    finally:
        app.run(lambda: app._card_generations.pop(card_id, None))
    return app.run(lambda: _write_generated_card(app, card_id, asked, text, stats))


def cancel_generation(app: App, card_id: int) -> str:
    """A card's generation given up — the model's ask cut, nothing
    written — and the sentence. Safe from any thread: it sets the run's
    flag alone. Raises Conflict where none is running: one can finish
    between the asking and this."""
    cancel = app._card_generations.get(card_id)
    if cancel is None:
        raise Conflict(_NO_GENERATION)
    cancel.set()
    return "Generation cancelled."


def _generation(app: App, card_id: int) -> _Generation:
    """The generation's ask, read off the card and the model in use."""
    card = get_card(app, card_id)
    model = app._model(ModelRole.WRITING)
    client = app._client(model)
    if model is None or client is None:
        raise Conflict(NO_MODEL_HINT)
    if client.pictures_ride(model.name) is PicturesRide.NONE:
        raise Conflict(_CANNOT_SEE)
    found = (app._store.files.get(picture.file) for picture in card.pictures)
    pictures = tuple(WirePicture(*file) for file in found if file is not None)
    if not pictures:
        raise Conflict(_NO_PICTURE)
    if card_id in app._card_generations:
        raise Conflict(f"{card.name} is being written already.")
    # The name the reader gave stands, and the model writes the card
    # around it; the placeholder is no name, so the model chooses one.
    prompt = render(
        app._prompts().card_from_picture_prompt,
        name="" if card.name == NEW_CARD_NAME else card.name,
    )
    cancel = app._card_generations[card_id] = threading.Event()
    return _Generation(
        model,
        client,
        WireTurn(role="user", body=prompt, images=pictures),
        len(pictures),
        cancel,
    )


def _generated(asked: _Generation) -> tuple[str, Stats | None]:
    """The model's answer, streamed and gathered, and what it cost —
    without thinking, as the lore pass asks; cut where the run is
    cancelled, a prefill in flight included."""
    parts: list[str] = []
    final: Stats | None = None
    try:
        stream = asked.client.completion.chat(
            asked.model.name,
            [asked.request],
            dict(_GENERATION_PARAMS),
            level="none",
            purpose="card",
            watched=False,
            on_idle=lambda: not asked.cancel.is_set(),
        )
        try:
            for chunk in stream:
                if asked.cancel.is_set():
                    break
                if isinstance(chunk, Text):
                    parts.append(chunk.text)
                elif isinstance(chunk, Stats):
                    final = chunk
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
    except ProviderError as e:
        raise Unavailable(str(e)) from e
    return "".join(parts), final


def _write_generated_card(
    app: App, card_id: int, asked: _Generation, text: str, stats: Stats | None
) -> str:
    """The answer written into the card, as it stands now — its name only
    while it is still the placeholder, a tagline kept to one line — what
    it cost recorded; the sentence. A cancelled run records what it cost
    and writes nothing."""
    if stats is not None:
        app._store.usage.record(
            asked.client.config.name,
            asked.model.name,
            "card",
            prompt_tokens=stats.prompt_tokens,
            completion_tokens=stats.completion_tokens,
            cached_tokens=stats.cached_tokens,
            duration_seconds=stats.total_seconds,
        )
    if asked.cancel.is_set():
        raise Conflict("The generation was cancelled.")
    try:
        answer = parse_json_object(text)
    except ValueError as e:
        raise Unavailable(f"The model's answer was no card: {e}.") from e
    written = {
        field: value.strip()
        for field in _GENERATED
        if isinstance(value := answer.get(field), str) and value.strip()
    }
    card = get_card(app, card_id)
    name = answer.get("name")
    renamed = card.name == NEW_CARD_NAME and isinstance(name, str) and bool(name.strip())
    if not written and not renamed:
        raise Unavailable("The model's answer was no card.")
    tagline = written.pop("tagline", None)
    update_card(
        app,
        card_id,
        name=name if renamed and isinstance(name, str) else None,
        tagline=" ".join(tagline.split()) if tagline is not None else None,
        changes=written or None,
    )
    count = asked.picture_count
    return f"Generated from {'its picture' if count == 1 else f'its {count} pictures'}."


# ---------- a story's characters ----------


def promote_character(
    app: App, story_id: int, character_id: int, *, with_copies: bool = False
) -> tuple[int, str]:
    """A card made from a story's character and linked to it — its name
    and aliases, the fields of the card it was imported from (the
    `{{user}}` binding left behind), else its description — and, with
    `with_copies`, linked to its copies too (`find_copies`); the new card's
    id, and the sentence. Raises NotFound for a story or a character
    that is not there, Conflict for one linked already."""
    character = _promotable(app, story_id, character_id)
    taken = [copy for _, copy in find_copies(app, story_id, character)] if with_copies else []
    # A copy edited past reading leaves the description to stand in; the
    # persona its story binds is the story's, not the card's.
    profile = replace(story_profile(character) or CharacterProfile(name=character.name), user="")
    if not profile.description:
        profile = replace(profile, description=character.description)
    card_id = app._store.cards.add(
        character.name,
        aliases=_clean_aliases(character.aliases, character.name),
        profile=profile.to_toml(),
    )
    app._store.characters.set_card_id(character.id, card_id, copies=taken)
    said = f"Added {character.name} to the codex, linked to this story's character."
    return card_id, said + linked_copies(len(taken))


def check_promotion(app: App, story_id: int, character_id: int) -> tuple[tuple[int, int], ...]:
    """A promotion vetted before it is made, refused as
    `promote_character` would be: the copies it may take along."""
    return find_copies(app, story_id, _promotable(app, story_id, character_id))


def find_copies(
    app: App, story_id: int, character: Character, *, card_id: int | None = None
) -> tuple[tuple[int, int], ...]:
    """A story character's copies, as (story, character) ids: in every
    story forks join its own to, the one unlinked character of its very
    name. A story holding two is passed over, as is one where `card_id`
    links another already — the link would be refused there."""
    found = []
    for other in app._store.stories.get_family(story_id):
        cast = app._store.characters.list(other)
        if card_id is not None and any(c.card_id == card_id for c in cast):
            continue
        named = [c.id for c in cast if c.card_id is None and c.name == character.name]
        if len(named) == 1:
            found.append((other, named[0]))
    return tuple(found)


def linked_copies(count: int) -> str:
    """What a link says of the copies it took along, after its own
    sentence; nothing for none."""
    if not count:
        return ""
    return f" Linked in {count} other {'story' if count == 1 else 'stories'} too."


def _promotable(app: App, story_id: int, character_id: int) -> Character:
    """The character a promotion makes a card of, refused as
    `promote_character` says."""
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
    return character


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


def _read(data: bytes, file_name: str) -> tuple[CharacterProfile, list[str]]:
    """The profile a card file holds and what reading it noted; Refused
    for a file that is no card."""
    try:
        return load_card(data, file_name=file_name)
    except CardError as e:
        raise Invalid(f"Not a character card: {e}") from e


def _digest(profile: CharacterProfile) -> str:
    """What tells one card file's card from another's: its profile as
    read, the file's name left out — the same card saved as PNG or as
    JSON, or under another name, is one card. `user` is no card's (and
    left out, so a digest reads as it did before it was a field)."""
    data = asdict(replace(profile, file_name=""))
    del data["user"]
    text = json.dumps(data, sort_keys=True, ensure_ascii=False)
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
