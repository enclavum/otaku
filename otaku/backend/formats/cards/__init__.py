"""Character-card FILES: the wild formats read into the one honest
`context.cards.CharacterProfile`, and a card written back as a file;
what a card SENDS is `context.cards`' business.

A card's text is the card spec's JSON (`_json`), whichever file carries
it: a PNG holds it in its text chunks (`_png`), a CHARX — the V3 spec's
zip — as its `card.json` beside its pictures (`_charx`), and a JSON file
is the text alone. Each of the three serves the import, then the export;
above them are the two sides, which this face re-exports:

    imports   a card file in: its card, what the import says, its pictures
    exports   a card out as a file: its original patched, or written whole

Everything here is pure: bytes and strings in, values out, no disk and
no model — the codex owns the store."""

from otaku.backend.formats.cards._charx import Asset, is_charx, pack_charx
from otaku.backend.formats.cards._json import CardError
from otaku.backend.formats.cards.exports import CardFileKind, write_card
from otaku.backend.formats.cards.imports import (
    has_embedded_cards,
    read_assets,
    read_card,
    read_main_picture,
)

__all__ = [
    "Asset",
    "CardError",
    "CardFileKind",
    "has_embedded_cards",
    "is_charx",
    "pack_charx",
    "read_assets",
    "read_card",
    "read_main_picture",
    "write_card",
]
