"""A card in a PNG: the card's text as base64 JSON in the picture's text
chunks — `chara` for V1/V2, `ccv3` for V3, the keyword matched
case-insensitively, `tEXt`, `zTXt` and `iTXt` alike. A PNG holds a LIST
of embedded cards, not one: real files carry the same card twice (V2
beside V3, or duplicate `chara` chunks that differ), and which copy is
read is the package's choice (`imports.read_card`). On export
(`write_cards`), the picture keeps every chunk it had but its cards',
and carries the ones given as `tEXt`, before its end."""

import base64
import json
import struct
import zlib

_MAGIC = b"\x89PNG\r\n\x1a\n"
_CARD_KEYWORDS = (b"chara", b"ccv3")


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
    pos = len(_MAGIC)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length  # length + type + payload + crc (never validated:
        # every real card has a good crc, and a bad one would only ever
        # cost a readable card)
        if ctype == b"IEND":
            break
        if ctype not in (b"tEXt", b"zTXt", b"iTXt"):
            continue
        keyword, _, rest = chunk.partition(b"\x00")
        if keyword.lower() not in _CARD_KEYWORDS:
            continue
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
            payload = json.loads(base64.b64decode(rest, validate=False))
        except (ValueError, zlib.error):
            continue  # a chunk that will not read is not the card
        if isinstance(payload, dict):
            out.append((keyword.decode("ascii", "replace").lower(), payload))
    return out


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
