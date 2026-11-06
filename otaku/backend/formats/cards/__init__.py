"""Character-card FILES: reading the wild formats into the one honest
`context.cards.CharacterProfile`. Beside `sillytavern` and `plaintext` on
purpose: every foreign artifact is decoded in this package; what a card
SENDS is `context.cards`' business.

A card comes in one of three files, each read by its own module — a PNG
with the card in its text chunks (`png`), a CHARX, the V3 spec's zip
(`charx`), or plain JSON — and whichever carries it, the card's text is
the one spec's JSON, read onto the profile by `spec`. This module is the
package's face: `load_card` tells the file, chooses among a PNG's copies
— `ccv3` first, as the V3 spec instructs a reader, else the FIRST
`chara` — and reports when they genuinely disagree, which duplicate
`chara` chunks in the wild do, and what the import leaves behind;
`card_picture` is the picture a file carries.

Everything here is pure: bytes and strings in, values out, no disk and
no model — the import operation owns the store."""

from dataclasses import replace

from otaku.backend.formats.cards import charx, png, spec
from otaku.backend.formats.cards.charx import is_charx, pack_charx
from otaku.backend.formats.cards.spec import CardError
from otaku.context.cards import CharacterProfile

__all__ = ["CardError", "card_picture", "has_embedded_cards", "is_charx", "load_card", "pack_charx"]


def load_card(data: bytes, *, file_name: str = "") -> tuple[CharacterProfile, list[str]]:
    """The card in `data` (a PNG, JSON or CHARX file's bytes) and the
    notes the import should say out loud, each a sentence — a lorebook or
    assets left behind, disagreeing embedded copies, macros nothing will
    bind. `file_name` (no path) rides the card as reference. Raises
    `CardError` when there is no card to have."""
    archive = None
    if png.is_png(data):
        candidates = png.embedded_cards(data)
        if not candidates:
            raise CardError("no character card embedded in this PNG")
    elif is_charx(data):
        archive = charx.open_archive(data)
        candidates = [("charx", charx.card_payload(archive))]
    else:
        candidates = [("json", spec.json_payload(data))]
    normalized = [(kw, payload, spec.normalize(payload)) for kw, payload in candidates]
    valid = [(kw, payload, card) for kw, payload, card in normalized if card is not None]
    if not valid:
        raise CardError("the card carries no character name")
    chosen = next(
        (entry for entry in valid if entry[0] == "ccv3"), valid[0]
    )  # the V3 spec: prefer ccv3; else the FIRST chara — never a dict's accident
    _, payload, card = chosen
    notes = []
    # Compared BEFORE the file name is stamped on: every candidate came
    # out of `normalize` bare, and a stamped copy would "disagree" with
    # its own identical twin on the name alone.
    if any(other != card for _, _, other in valid):
        notes.append(
            f"The file holds {len(valid)} versions of the card that disagree; "
            f"the {chosen[0]} one is imported."
        )
    if file_name:
        card = replace(card, file_name=file_name)
    if spec.has_lorebook(payload):
        notes.append("The card's lorebook isn't imported.")
    if archive is not None:
        left = charx.other_assets(archive, payload)
        if left:
            notes.append(f"The card's other {left} files aren't imported.")
    unknown = spec.unknown_macros(card)
    if unknown:
        notes.append(f"{', '.join(unknown)} {'stays' if len(unknown) == 1 else 'stay'} as written.")
    return card, notes


def has_embedded_cards(data: bytes) -> bool:
    """Whether a PNG's bytes carry a card at all — a chunk one could be
    read from. A PNG without one is a picture alone."""
    return bool(png.embedded_cards(data))


def card_picture(data: bytes) -> bytes | None:
    """The picture a card file carries: a PNG is its own, a CHARX's is its
    main icon — the icon named `main`, else the first the card lists, else
    the first in its icon folder; None for JSON, and for a CHARX with no
    icon or one that will not read."""
    if png.is_png(data):
        return data
    if not is_charx(data):
        return None
    try:
        archive = charx.open_archive(data)
        path = charx.icon_path(archive, charx.card_payload(archive))
        return charx.member(archive, path) if path is not None else None
    except CardError:
        return None
