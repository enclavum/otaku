"""A card in a PNG: the card's text as base64 JSON in the picture's text
chunks — `chara` for V1/V2, `ccv3` for V3, the keyword matched
case-insensitively, `tEXt`, `zTXt` and `iTXt` alike. A PNG holds a LIST
of embedded cards, not one: real files carry the same card twice (V2
beside V3, or duplicate `chara` chunks that differ), and which copy is
read is the package's choice (`imports.read_card`). RisuAI keeps a
card's other files in the PNG too, a chunk each (`chara-ext-asset_:N`,
the file in base64), which the card's text names `__asset:N` — in a V3
card's assets, or a V2 card's own RisuAI listing; those that are
pictures are the card's other pictures. On export
(`write_cards`), the picture keeps every chunk it had but its cards',
and carries the ones given as `tEXt`, before its end."""

import base64
import binascii
import json
import struct
import zlib
from collections.abc import Iterator

from otaku.backend.formats.cards._json import Asset, list_assets, list_risu_assets

_MAGIC = b"\x89PNG\r\n\x1a\n"
_CARD_KEYWORDS = (b"chara", b"ccv3")
# RisuAI's chunk for one of a card's other files, `:N` or `N` after it,
# and how the card's text names that file.
_ASSET_KEYWORD = b"chara-ext-asset_"
_ASSET_URI = "__asset:"
# The card's own picture, as a V3 card names it: the PNG itself.
_DEFAULT_URI = "ccdefault:"
# A picture, by its first bytes: PNG, JPEG, GIF; WebP and HEIF have their own checks.
_PICTURE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8")


# ---------- import ----------


def is_png(data: bytes) -> bool:
    """Whether a file is a PNG — a picture, which only a card's chunk makes
    a card."""
    return data.startswith(_MAGIC)


def read_cards(data: bytes) -> list[tuple[str, dict[str, object]]]:
    """Every embedded card, in file order, keyword-tagged — a list, never
    a dict: real PNGs carry duplicate keywords with differing payloads,
    and a dict would silently keep whichever assignment came last."""
    out: list[tuple[str, dict[str, object]]] = []
    for keyword, text in _text_chunks(data):
        if keyword not in _CARD_KEYWORDS:
            continue
        try:
            payload = json.loads(base64.b64decode(text, validate=False))
        except ValueError:
            continue  # a chunk that will not read is not the card
        if isinstance(payload, dict):
            out.append((keyword.decode("ascii", "replace"), payload))
    return out


def read_pictures(data: bytes, payload: dict[str, object]) -> list[Asset]:
    """Every picture the card keeps in its PNG's chunks, in the card's
    order, each once: those its V3 assets name, else those its V2 RisuAI
    listing does — the files whose bytes are a picture, the PNG's own
    left to be the main one."""
    chunks = _asset_chunks(data)
    out: list[Asset] = []
    seen: set[str] = set()
    for kind, name, key in _listed(payload):
        found = chunks.get(key)
        if found is None or key in seen or not _is_picture(found):
            continue
        seen.add(key)
        out.append(Asset(kind, name, found))
    return out


def count_left_behind(data: bytes, payload: dict[str, object]) -> int:
    """How many of the files the card keeps in its PNG's chunks are no
    picture — what an import leaves behind."""
    chunks = _asset_chunks(data)
    keys = {key for _, _, key in _listed(payload) if key in chunks}
    return sum(1 for key in keys if not _is_picture(chunks[key]))


def _text_chunks(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    """Every text chunk before the end, its keyword lowercased and its
    text inflated — `tEXt`, `zTXt` and `iTXt` alike; one that will not
    inflate passed over."""
    pos = len(_MAGIC)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length  # length + type + payload + crc (never validated:
        # every real card has a good crc, and a bad one would only ever
        # cost a readable card)
        if ctype == b"IEND":
            return
        if ctype not in (b"tEXt", b"zTXt", b"iTXt"):
            continue
        keyword, _, rest = chunk.partition(b"\x00")
        try:
            if ctype == b"zTXt":
                rest = zlib.decompress(rest[1:])  # one compression-method byte first
            elif ctype == b"iTXt":
                compressed = rest[:1] == b"\x01"
                rest = rest[2:]  # compression flag + method
                _, _, rest = rest.partition(b"\x00")  # language tag
                _, _, rest = rest.partition(b"\x00")  # translated keyword
                if compressed:
                    rest = zlib.decompress(rest)
        except zlib.error:
            continue
        yield keyword.lower(), rest


def _asset_chunks(data: bytes) -> dict[str, bytes]:
    """RisuAI's files in the PNG, by the N their chunk is named with; one
    that will not decode passed over."""
    out: dict[str, bytes] = {}
    for keyword, text in _text_chunks(data):
        if not keyword.startswith(_ASSET_KEYWORD):
            continue
        key = keyword.removeprefix(_ASSET_KEYWORD).removeprefix(b":").decode("ascii", "replace")
        try:
            out[key] = base64.b64decode(text, validate=False)
        except (ValueError, binascii.Error):
            continue
    return out


def _listed(payload: dict[str, object]) -> list[tuple[str, str, str]]:
    """The files the card's text names in its PNG's chunks, in its order,
    each as its type, its name and its chunk's N: a V3 card's assets,
    else a V2 card's RisuAI listing — the PNG's own picture left out."""
    entries = list_assets(payload) or list_risu_assets(payload)
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        uri = entry.get("uri")
        if not isinstance(uri, str) or uri == _DEFAULT_URI or not uri.startswith(_ASSET_URI):
            continue
        kind, name = entry.get("type"), entry.get("name")
        out.append(
            (
                kind if isinstance(kind, str) else "",
                name if isinstance(name, str) else "",
                uri.removeprefix(_ASSET_URI),
            )
        )
    return out


def _is_picture(data: bytes) -> bool:
    """Whether a file's bytes are a picture: PNG, JPEG, GIF, WebP, or
    HEIF/AVIF — never a sound or a model RisuAI keeps beside them."""
    if data.startswith(_PICTURE_MAGIC):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"avif", b"avis")


# ---------- export ----------


def write_cards(data: bytes, cards: list[tuple[str, dict[str, object]]]) -> bytes:
    """The PNG `data` carrying `cards` — each a keyword (`chara`, `ccv3`)
    and a card's text — as its only cards: every card chunk it had
    dropped, every other chunk kept as it was, the new ones before its
    end."""
    kept: list[bytes] = []
    pos = len(_MAGIC)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        whole = data[pos : pos + 12 + length]
        keyword = data[pos + 8 : pos + 8 + length].partition(b"\x00")[0]
        pos += 12 + length
        if ctype == b"IEND":
            break
        if ctype in (b"tEXt", b"zTXt", b"iTXt") and keyword.lower() in _CARD_KEYWORDS:
            continue
        kept.append(whole)
    written = [
        _chunk(
            b"tEXt", keyword.encode("ascii") + b"\x00" + base64.b64encode(json.dumps(card).encode())
        )
        for keyword, card in cards
    ]
    return _MAGIC + b"".join(kept) + b"".join(written) + _chunk(b"IEND", b"")


def _chunk(ctype: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(ctype + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", crc)
