"""A card file read: the card it carries, onto the one honest
`context.cards.CharacterProfile`, and its pictures. `read_card` tells the
file, chooses among a PNG's copies — `ccv3` first, as the V3 spec
instructs a reader, else the FIRST `chara` — and reports when they
genuinely disagree, which duplicate `chara` chunks in the wild do, and
what the import leaves behind; `read_main_picture` is the picture a file
carries, and `read_assets` the others a CHARX does, each with its
type."""

from dataclasses import replace
from typing import TypeVar

from otaku.backend.formats.cards import _charx, _json, _png
from otaku.backend.formats.cards._charx import is_charx
from otaku.backend.formats.cards._json import Asset, CardError
from otaku.context.cards import CharacterProfile

# A copy of a card a file holds, its keyword first.
_Copy = TypeVar("_Copy", bound=tuple[object, ...])


def read_card(data: bytes, *, file_name: str = "") -> tuple[CharacterProfile, list[str]]:
    """The card in `data` (a PNG, JSON or CHARX file's bytes) and the
    notes the import should say out loud, each a sentence — a lorebook or
    assets left behind, disagreeing embedded copies. `file_name` (no
    path) rides the card as reference. Raises `CardError` when there is
    no card to have."""
    archive = None
    if _png.is_png(data):
        candidates = _png.read_cards(data)
        if not candidates:
            raise CardError("no character card embedded in this PNG")
    elif is_charx(data):
        archive = _charx.open_archive(data)
        candidates = [("charx", _charx.read_text(archive))]
    else:
        candidates = [("json", _json.read_text(data))]
    normalized = [(kw, payload, _json.read_profile(payload)) for kw, payload in candidates]
    valid = [(kw, payload, card) for kw, payload, card in normalized if card is not None]
    if not valid:
        raise CardError("the card carries no character name")
    chosen = _preferred(valid)
    _, payload, card = chosen
    notes = []
    # Compared BEFORE the file name is stamped on: every candidate came
    # out of `read_profile` bare, and a stamped copy would "disagree" with
    # its own identical twin on the name alone.
    if any(other != card for _, _, other in valid):
        notes.append(
            f"The file holds {len(valid)} versions of the card that disagree; "
            f"the {chosen[0]} one is imported."
        )
    if file_name:
        card = replace(card, file_name=file_name)
    if _json.has_lorebook(payload):
        notes.append("The card's lorebook isn't imported.")
    left = 0
    if archive is not None:
        left = _charx.count_left_behind(archive, payload)
    elif _png.is_png(data):
        left = _png.count_left_behind(data, payload)
    if left:
        files = "file isn't" if left == 1 else "files aren't"
        notes.append(f"The card's {left} other {files} imported.")
    return card, notes


def has_embedded_cards(data: bytes) -> bool:
    """Whether a PNG's bytes carry a card at all — a chunk one could be
    read from. A PNG without one is a picture alone."""
    return bool(_png.read_cards(data))


def read_main_picture(data: bytes) -> bytes | None:
    """The picture a card file carries: a PNG is its own, a CHARX's is its
    main icon — the icon named `main`, else the first the card lists, else
    the first in its icon folder; None for JSON, and for a CHARX with no
    icon or one that will not read."""
    if _png.is_png(data):
        return data
    if not is_charx(data):
        return None
    try:
        archive = _charx.open_archive(data)
        path = _charx.icon_path(archive, _charx.read_text(archive))
        return _charx.read_member(archive, path) if path is not None else None
    except CardError:
        return None


def read_assets(data: bytes) -> list[Asset]:
    """The pictures a card file carries beside its main one, each with the
    type and name the card gives it, in the card's order: a CHARX's, and
    those RisuAI keeps in a PNG's chunks, as the copy `read_card` reads
    names them; none for JSON, and for a file that will not read."""
    if _png.is_png(data):
        named = [entry for entry in _png.read_cards(data) if _json.read_profile(entry[1])]
        return _png.read_pictures(data, _preferred(named)[1]) if named else []
    if not is_charx(data):
        return []
    try:
        archive = _charx.open_archive(data)
        return _charx.read_pictures(archive, _charx.read_text(archive))
    except CardError:
        return []


def _preferred(entries: list[_Copy]) -> _Copy:
    """The copy of a card a file's copies are read as: the V3 spec's own
    rule, `ccv3` first, else the FIRST — never a dict's accident."""
    return next((entry for entry in entries if entry[0] == "ccv3"), entries[0])
