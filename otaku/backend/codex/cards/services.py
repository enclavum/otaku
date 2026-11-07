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

A card file comes from a link too — a card site's character page or the
file's own link — fetched into memory (`fetch_card`) and imported
as any file is, by either door.

A card file is the codex's ALREADY when the digest of the card it holds —
its fields as read, the file's name left out — is among a card's
`digests`; a picture alone, when the digest of the picture is (a blank
card keeps its picture's): the import makes nothing then and answers
that card. A card ALIKE — of the same name, or holding the same picture,
by the digest its stored file is named by — is only offered
(`CheckedCardFile.similar_cards`): it is the same card where the reader
says so (`reuse`), which notes the file's digest on it, and otherwise
the import makes a card beside it.
"""

import hashlib
import io
import json
import re
import threading
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from pathlib import PurePosixPath
from typing import Any, cast

from PIL import Image

from otaku import __version__
from otaku.backend.app.core.services import App
from otaku.backend.app.pictures.services import IntakePicture, RawFile, read_picture, save
from otaku.backend.errors import (
    NO_MODEL_HINT,
    NO_SUCH_STORY,
    Conflict,
    Invalid,
    NotFound,
    Refused,
    Unavailable,
)
from otaku.backend.formats.cards import (
    Asset,
    CardError,
    CardFileKind,
    has_embedded_cards,
    is_charx,
    pack_charx,
    read_assets,
    read_card,
    read_main_picture,
    write_card,
)
from otaku.context.cards import CharacterProfile
from otaku.context.prompt import WirePicture, WireTurn
from otaku.formatting import parse_json_object, render
from otaku.integrations import Cards, CardSourceError, IntegrationError
from otaku.providers import OpenAIClient, PicturesRide, ProviderError, Stats, Text
from otaku.settings.state import ModelRef, ModelRole
from otaku.store.files import Owner, digest, digest_of
from otaku.store.ops.lore import CastListing
from otaku.store.pictures import CardPicture, CardPictureKind
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
_NOT_A_CARD = "Not a card."
_BLANK_CARD = "Picture only, without a card."
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_UTF8_BOM = b"\xef\xbb\xbf"
# A picture alone among a card's digests, marked apart from a card
# text's — its whole digest, the stored file's name carrying a short one.
_PICTURE_DIGEST = "picture:"
# Why a card is alike, by what matches: its name, its picture, or both.
# What a card file's picture shows, by the type the card gives it (the
# V3 spec's asset types); any other type is another kind of picture.
_KINDS = {
    "icon": CardPictureKind.PORTRAIT,
    "emotion": CardPictureKind.EXPRESSION,
    "background": CardPictureKind.BACKGROUND,
}
# A card file kept as it came: its extension, by what it is.
_ORIGINAL_PNG, _ORIGINAL_CHARX, _ORIGINAL_JSON = "png", "charx", "json"
# What a card is exported as — the kinds of card file, and the one it came
# as — and each one's media type.
EXPORT_FORMATS = ("png", "json", "charx", "original")
_MEDIA = {"png": "image/png", "json": "application/json", "charx": "application/zip"}
# A picture's kind as a card file names it, an asset's type.
_ASSET_TYPES = {
    CardPictureKind.PORTRAIT: "icon",
    CardPictureKind.EXPRESSION: "emotion",
    CardPictureKind.BACKGROUND: "background",
    CardPictureKind.OTHER: "other",
}
# What no file name may hold, on any system a card is saved to.
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_ALIKE = {
    (True, False): "Same name",
    (False, True): "Same picture",
    (True, True): "Same name and picture",
}

# What a generation writes, besides the name: the card's text as the
# model reads a picture into it.
_GENERATED = ("tagline", "description", "personality", "scenario", "greeting")
# How a generation is asked: warm enough to invent what a picture leaves
# open, and bounded, as the lore pass is, against a looping model.
_GENERATION_PARAMS: dict[str, object] = {"temperature": 0.7, "max_tokens": 2000}
_CANNOT_SEE = "The model in use cannot see pictures."
# The language a generation writes in where none is asked, and the
# longest name one may have: a language, never a sentence.
DEFAULT_LANGUAGE = "English"
_MAX_LANGUAGE = 40
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
    card would be made of (under the name asked, where one is), what reading it noted,
    whether it carries a picture and that picture's thumbnail (None where
    it has none, or the intake refuses it), the card the codex already
    holds it as (None for none), and the cards alike the reader may say
    it is, each with why (`alike`, by card id: "Same name", "Same
    picture", or both)."""

    profile: CharacterProfile
    notes: tuple[str, ...]
    has_picture: bool
    thumbnail: bytes | None
    same_card: Card | None
    similar_cards: tuple[Card, ...]
    alike: Mapping[int, str]


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
    return card_id, f"Added {name} to Characters."


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
    card's portraits by name, in the order they now stand, the first the
    main one, its other pictures keeping theirs after them
    (`delete_card_picture` removes one). Raises Refused for nothing to
    change, an emptied name, a tagline of more than one line, a field the
    card has not, a reminder's depth below 1, and an order that is not
    the card's portraits each once."""
    card = get_card(app, card_id)
    if all(part is None for part in (name, aliases, tagline, changes, pictures)):
        raise Invalid("Nothing to change.")
    new_name = _clean_name(name) if name is not None else card.name
    new_profile: str | None = None
    if changes:
        new_profile = corrected_profile(card_profile(card), changes).to_toml()
    ordered = None
    if pictures is not None:
        portraits, others = _split(card.pictures)
        by_name = {picture.file: picture for picture in portraits}
        if sorted(pictures) != sorted(by_name):
            raise Invalid("A new order names each of the card's pictures once.")
        ordered = [*(by_name[file] for file in pictures), *others]
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
    said = f"Deleted {card.name} from Characters."
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


def is_blank(profile: CharacterProfile) -> bool:
    """Whether a card holds no text a story could take — every field the
    reader writes empty, its reminder's depth aside: a card made from a
    picture, before it is written."""
    return not any(getattr(profile, name) for name in FIELDS if name != "reminder_depth")


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
    """A portrait added to a card, after its others — read through the one
    intake, stored under the card's number — and the sentence. Raises
    Refused for a picture the intake refuses, and one the card already
    has."""
    card = get_card(app, card_id)
    saved = save(app._store.files, read_picture(file), card_id, CardPicture)
    if any(picture.file == saved.file for picture in card.pictures):
        raise Conflict(f"{card.name} has this picture already.")
    portraits, others = _split(card.pictures)
    app._store.cards.set_pictures(card_id, [*portraits, saved, *others])
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
    name: str | None = None,
    allow_bare_picture: bool = False,
) -> CheckedCardFile:
    """A card file read for the codex, nothing stored: the card it holds
    — under `name`, where one is asked — and what reading it noted,
    whether it carries a picture, and the cards the codex may already
    hold it as — or, with `allow_bare_picture`, a picture that holds no
    card as the blank card it would make, the picture vetted by the one
    intake. Raises Refused for a file that is no card, nor a picture
    where one is allowed, and a name asked that is blank."""
    if allow_bare_picture and _is_bare_picture(data):
        picture = read_picture(RawFile(data, file_name))
        same = app._store.cards.find_by_digest(_PICTURE_DIGEST + _whole_digest(picture.data))
        alike = _alike(app, set(), picture.data, same)
        return CheckedCardFile(
            profile=CharacterProfile(name=_clean_name(name) if name is not None else NEW_CARD_NAME),
            notes=(_BLANK_CARD,) if same is None else (),
            has_picture=True,
            thumbnail=picture.thumb,
            same_card=same,
            similar_cards=tuple(card for card, _ in alike),
            alike={card.id: why for card, why in alike},
        )
    profile, notes = _read(data, file_name)
    same = app._store.cards.find_by_digest(_digest(profile))
    named = replace(profile, name=_clean_name(name)) if name is not None else profile
    carried = carried_picture(data, file_name)
    alike = _alike(
        app,
        {named.name.casefold(), profile.name.casefold()},
        carried.data if carried is not None else None,
        same,
    )
    return CheckedCardFile(
        profile=named,
        notes=tuple(notes),
        has_picture=read_main_picture(data) is not None,
        thumbnail=carried.thumb if carried is not None else None,
        same_card=same,
        similar_cards=tuple(card for card, _ in alike),
        alike={card.id: why for card, why in alike},
    )


def similar_note(checked: CheckedCardFile) -> str:
    """What a card file the codex imports says of the cards alike — it is
    made beside them — as a sentence; "" for none."""
    if not checked.similar_cards:
        return ""
    named = ", ".join(
        f"{card.name} ({checked.alike[card.id].lower()})" for card in checked.similar_cards
    )
    many = len(checked.similar_cards) > 1
    return (
        f"Characters has {'cards' if many else 'a card'} like this: {named}. "
        f"It will be added beside {'them' if many else 'it'}."
    )


def carried_picture(data: bytes, file_name: str) -> IntakePicture | None:
    """The picture a card file carries, as the intake stores it — what a
    story keeps of a card it alone holds; None for none, and for one the
    intake refuses."""
    carried = read_main_picture(data)
    if carried is None:
        return None
    try:
        return read_picture(RawFile(carried, file_name))
    except Refused:
        return None


def import_card_file(
    app: App,
    data: bytes,
    file_name: str,
    *,
    name: str | None = None,
    reuse: int | None = None,
    tagline: str = "",
    allow_bare_picture: bool = False,
) -> tuple[int, bool, str]:
    """A card file into the codex — the card it is there, whether it was
    made now, and the sentence. A file the codex holds already makes
    nothing; `reuse` names the card the reader says it is, which notes
    the file's digest. Otherwise a card is made — under `name`, its own
    where none is asked, `tagline` its own (a site's, the file came from
    a link to) — and the
    picture the file carries kept as its portrait (a PNG's own, a
    CHARX's main icon); a picture the intake refuses leaves the card
    without one, and the sentence says so. With
    `allow_bare_picture`, a picture that holds no card makes a blank card
    — under `name`, else `NEW_CARD_NAME` — the picture its portrait.
    Raises Refused for a file that is no card, nor a picture where one is
    allowed, and a `reuse` the codex does not hold."""
    if allow_bare_picture and _is_bare_picture(data):
        picture = read_picture(RawFile(data, file_name))
        known = _PICTURE_DIGEST + _whole_digest(picture.data)
        same = app._store.cards.find_by_digest(known)
        if same is not None:
            return same.id, False, f"{same.name} is in Characters already."
        named = _clean_name(name) if name is not None else NEW_CARD_NAME
        card_id = app._store.cards.add(
            named,
            tagline=_clean_tagline(tagline),
            profile=CharacterProfile(name=named).to_toml(),
            digests=(known,),
        )
        app._store.cards.set_pictures(
            card_id, [save(app._store.files, picture, card_id, CardPicture)]
        )
        return card_id, True, f"Added {named} to Characters, the picture its portrait."
    profile, notes = _read(data, file_name)
    digest = _digest(profile)
    same = app._store.cards.find_by_digest(digest)
    if same is not None:
        return same.id, False, f"{same.name} is in Characters already."
    if reuse is not None:
        kept = get_card(app, reuse)
        app._store.cards.add_digest(kept.id, digest)
        return kept.id, False, f"Kept {kept.name}: Characters has it already."
    named = _clean_name(name) if name is not None else profile.name
    card_id = app._store.cards.add(
        named, tagline=_clean_tagline(tagline), profile=profile.to_toml(), digests=(digest,)
    )
    said = [f"Added {named} to Characters.", *notes]
    app._store.cards.set_original_file(
        card_id, app._store.files.add_file(Owner.CARD, card_id, data, _original_extension(data))
    )
    pictures: list[CardPicture] = []
    carried = read_main_picture(data)
    if carried is not None:
        try:
            picture = read_picture(RawFile(carried, file_name))
        except Refused as e:
            said.append(f"The picture isn't kept. {e}")
        else:
            pictures.append(save(app._store.files, picture, card_id, CardPicture))
    refused = 0
    for asset in read_assets(data):
        try:
            picture = read_picture(RawFile(asset.data, asset.name or file_name))
        except Refused:
            refused += 1
            continue
        saved = save(app._store.files, picture, card_id, CardPicture)
        if all(kept.file != saved.file for kept in pictures):
            kind = _KINDS.get(asset.type, CardPictureKind.OTHER)
            pictures.append(replace(saved, kind=kind, name=asset.name))
    if refused:
        said.append(f"{refused} of the card's other pictures aren't kept.")
    if pictures:
        portraits, others = _split(pictures)
        app._store.cards.set_pictures(card_id, [*portraits, *others])
    return card_id, True, " ".join(said)


def _split(pictures: Sequence[CardPicture]) -> tuple[list[CardPicture], list[CardPicture]]:
    """A card's portraits and its other pictures, each in their order."""
    portraits = [picture for picture in pictures if picture.kind is CardPictureKind.PORTRAIT]
    others = [picture for picture in pictures if picture.kind is not CardPictureKind.PORTRAIT]
    return portraits, others


def _original_extension(data: bytes) -> str:
    """The extension a card file is kept under, by what it is."""
    if data.startswith(_PNG_MAGIC):
        return _ORIGINAL_PNG
    return _ORIGINAL_CHARX if is_charx(data) else _ORIGINAL_JSON


def _is_bare_picture(data: bytes) -> bool:
    """Whether a file is a picture that holds no card: a PNG without one,
    or anything that is no CHARX and no JSON object, a byte-order mark
    before it or not. What it is past that, the intake says."""
    if data.startswith(_PNG_MAGIC):
        return not has_embedded_cards(data)
    return not is_charx(data) and not data.removeprefix(_UTF8_BOM).lstrip().startswith(b"{")


# ---------- a card exported ----------


@dataclass(frozen=True)
class ExportedFile:
    """A card as a file: the name it is saved under, its bytes, and their
    media type."""

    name: str
    data: bytes
    media_type: str


def available_export_formats(card: Card) -> tuple[str, ...]:
    """What a card can be exported as, in `EXPORT_FORMATS`' order: PNG
    where it has a portrait to carry, JSON and CHARX always, the file it
    came as where it keeps one."""
    return tuple(
        written
        for written in EXPORT_FORMATS
        if (written != "png" or card.portraits) and (written != "original" or card.original_file)
    )


def original_format(card: Card) -> str | None:
    """The kind of file a card came as and keeps — `png`, `json` or
    `charx` — None for one that keeps none."""
    return _original_kind(card.original_file) if card.original_file else None


def default_export_format(card: Card) -> str:
    """What a card is exported as unless another is asked: the kind of
    file it came as, with otaku's edits; else PNG where it has a
    portrait, and JSON where it has none."""
    own = _original_kind(card.original_file) if card.original_file else None
    if own is not None and own in available_export_formats(card):
        return own
    return "png" if card.portraits else "json"


def export_card(app: App, card_id: int, written: str) -> ExportedFile:
    """A card as a file of the `written` kind (`EXPORT_FORMATS`): the file
    it came as, with the fields otaku changed laid over it — or that file
    as it came, `original` — or, with no file kept, the card written
    whole (`formats.cards.write_card`). Its name is the card's. Raises
    NotFound for a card the codex does not hold, Invalid for a kind that
    is none of the formats, Conflict for a PNG of a card with no portrait
    and an original it keeps none of."""
    card = get_card(app, card_id)
    if written not in EXPORT_FORMATS:
        raise Invalid("A card is exported as PNG, JSON, CHARX or the file it came as.")
    if written == "png" and not card.portraits:
        raise Conflict(_NO_PICTURE)
    original = app._store.files.get_file(card.original_file) if card.original_file else None
    if written == "original":
        if original is None or card.original_file is None:
            raise Conflict(f"{card.name} keeps no file it came as.")
        kind = _original_kind(card.original_file)
        return ExportedFile(
            _export_name(card.name, kind), original, _MEDIA.get(kind, "application/octet-stream")
        )
    standing = original is not None and _main_stands(card, original)
    pictures = _export_pictures(app, card)
    if written == "png":
        if not pictures:
            raise Conflict(_NO_PICTURE)
        # the file's own picture where it still is the card's, at its size
        own = original if standing and original and original.startswith(_PNG_MAGIC) else None
        pictures[0] = Asset("icon", pictures[0].name, own or _as_png(pictures[0].data))
    every = (
        standing and original is not None and len(card.pictures) == 1 + len(read_assets(original))
    )
    data = write_card(
        cast(CardFileKind, written),
        card_profile(card),
        card.tagline,
        original=original,
        pictures=pictures,
        keep_pictures=every,
    )
    return ExportedFile(_export_name(card.name, written), data, _MEDIA[written])


def _export_pictures(app: App, card: Card) -> list[Asset]:
    """A card's pictures as a card file carries them, in order — its
    portraits first — each of its asset type, a file the folder no longer
    opens left out."""
    out = []
    for picture in card.pictures:
        found = app._store.files.get_picture(picture.file)
        if found is not None:
            out.append(Asset(_ASSET_TYPES[picture.kind], picture.name, found[0]))
    return out


def _main_stands(card: Card, original: bytes) -> bool:
    """Whether a card's main portrait is still the picture its file
    brought, as the intake keeps it."""
    carried = carried_picture(original, "")
    if carried is None or not card.portraits:
        return False
    return digest_of(card.portraits[0].file) == digest(carried.data)


def _as_png(data: bytes) -> bytes:
    """A stored picture as a PNG — which it is already where the intake
    kept it so."""
    if data.startswith(_PNG_MAGIC):
        return data
    out = io.BytesIO()
    with Image.open(io.BytesIO(data)) as picture:
        picture.save(out, "PNG")
    return out.getvalue()


def _original_kind(name: str) -> str:
    """The kind of file a kept card file is, by its extension."""
    return PurePosixPath(name).suffix.lstrip(".").lower()


def _export_name(card_name: str, kind: str) -> str:
    """The name a card's file is saved under: the card's own, what no file
    name may hold taken out, and the kind's extension."""
    safe = _UNSAFE_NAME.sub(" ", card_name).strip() or "card"
    return f"{safe}.{kind}"


# ---------- a card from a link ----------


@dataclass(frozen=True)
class FetchedCardFile:
    """The card file a link gave — its bytes and the name it goes by —
    and the line its site shows it under, one line ("" for none)."""

    data: bytes
    name: str
    tagline: str = ""


def fetch_card(app: App, url: str) -> FetchedCardFile:
    """The card file a link gives — a card site's character page, or the
    file's own link — fetched into memory; a JSON card the site keeps
    its picture beside packed with it into one CHARX. Safe from any
    thread, and slow on purpose: it asks the site, never the store.
    Raises Invalid for a link no card is fetched from, Unavailable for
    a site that could not be reached, refused, or answered no card."""
    try:
        got = app._integrations.get(Cards).fetch(url, user_agent=f"otaku/{__version__}")
    except CardSourceError as e:
        raise Invalid(str(e)) from e
    except IntegrationError as e:
        raise Unavailable(str(e)) from e
    tagline = " ".join(got.tagline.split())
    if got.picture is None:
        return FetchedCardFile(got.data, got.file_name, tagline)
    packed = PurePosixPath(got.file_name).with_suffix(".charx").name
    return FetchedCardFile(pack_charx(got.data, got.picture), packed, tagline)


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


def generate_card(app: App, card_id: int, *, language: str = DEFAULT_LANGUAGE) -> str:
    """A card written by the model in use from its pictures — every one,
    in order — and the prompt (`card_from_picture_prompt`), in `language`
    (its English name, as the model reads it best): its tagline,
    description, personality, scenario and greeting replaced, and its
    name while it is still `NEW_CARD_NAME`; the sentence. Safe from any
    thread, and slow on purpose: the card is read and written on the
    app's thread (`App.run`), the model asked off it — until
    `cancel_generation`, which cuts the ask and writes nothing. Raises
    NotFound for a card the codex does not hold; Invalid for a language
    that is no short line; Conflict while no model is in use, the model
    cannot see pictures, the card has none or is being written already,
    and for a generation cancelled; Unavailable for what the engine
    refuses and an answer that is no card."""
    wanted = language.strip()
    if not wanted or "\n" in wanted or len(wanted) > _MAX_LANGUAGE:
        raise Invalid("A language is named in one short line.")
    asked = app.run(lambda: _generation(app, card_id, wanted))
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


def _generation(app: App, card_id: int, language: str) -> _Generation:
    """The generation's ask, read off the card and the model in use, in
    `language`."""
    card = get_card(app, card_id)
    model = app._model(ModelRole.WRITING)
    client = app._client(model)
    if model is None or client is None:
        raise Conflict(NO_MODEL_HINT)
    if client.pictures_ride(model.name) is PicturesRide.NONE:
        raise Conflict(_CANNOT_SEE)
    found = (app._store.files.get_picture(picture.file) for picture in card.portraits)
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
        language=language,
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
    said = f"Added {character.name} to Characters, linked to this story's character."
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
        raise Conflict(f"This character is linked to {linked.name} in Characters already.")
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
        return read_card(data, file_name=file_name)
    except CardError as e:
        raise Invalid(_NOT_A_CARD) from e


def _digest(profile: CharacterProfile) -> str:
    """What tells one card file's card from another's: its profile as
    read, the file's name left out — the same card saved as PNG or as
    JSON, or under another name, is one card. `user` is no card's (and
    left out, so a digest reads as it did before it was a field)."""
    data = asdict(replace(profile, file_name=""))
    del data["user"]
    text = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.blake2b(text.encode(), digest_size=16).hexdigest()


def _whole_digest(data: bytes) -> str:
    """A picture's digest as a card's digests keep it: whole, where its
    file's name carries a short one."""
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _alike(
    app: App, names: set[str], picture: bytes | None, same: Card | None
) -> list[tuple[Card, str]]:
    """The cards alike, but `same`, in the reader's order, each with why:
    a name or an alias among `names` (casefolded), a picture stored as
    `picture` is (its digest, as its file is named)."""
    wanted = digest(picture) if picture is not None else None
    found = []
    for card in list_cards(app):
        if same is not None and card.id == same.id:
            continue
        named = bool(names & {card.name.casefold(), *(a.casefold() for a in card.aliases)})
        pictured = wanted is not None and any(digest_of(p.file) == wanted for p in card.portraits)
        if named or pictured:
            found.append((card, _ALIKE[(named, pictured)]))
    return found


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
