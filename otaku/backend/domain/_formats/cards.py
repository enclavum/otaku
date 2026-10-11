"""Character-card FILES: reading the wild formats (a PNG with base64
JSON in its text chunks, or plain JSON) into the one honest
`context.cards.Card`. Beside `sillytavern` and `plaintext` on purpose:
every foreign artifact is decoded in this package; what a card SENDS is
`context.cards`' business.

A card arrives as JSON or as a PNG with base64 JSON in its text chunks
(`chara` for V1/V2, `ccv3` for V3 — matched case-insensitively, `tEXt`,
`zTXt` and `iTXt` alike). A PNG holds a LIST of embedded cards, not
one: real files carry the same card twice (V2 beside V3, or duplicate
`chara` chunks that differ), so `load_card` returns candidates through
an explicit selection — `ccv3` first, as the V3 spec instructs a
reader, else the FIRST `chara` — and reports when the copies genuinely
disagree, which duplicate `chara` chunks in the wild do.

Normalization is an ALLOWLIST: the fields below are read, everything
else drops on the floor — the format leaks runtime junk (`chat`,
`char_persona`) and a denylist can never keep up. `{{char}}`/`{{user}}`
macros stay UNBOUND here: binding needs the chosen character name and
the player's, which only the import command knows. Line endings are
normalized to `\\n` — cards mix them, even within one file — and
`mes_example` is split on its `<START>` delimiter into separate example
blocks, because passed through whole the model reads the delimiter as
literal text.

Everything here is pure: bytes and strings in, values out, no disk and
no model — the import operation owns the store.
"""

import base64
import json
import re
import struct
import zlib
from dataclasses import replace as _replace

from otaku.context.cards import Card, binds

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_CARD_KEYWORDS = (b"chara", b"ccv3")

_ANY_MACRO = re.compile(r"\{\{[^{}]{1,40}\}\}")
_START = re.compile(r"<START>", re.IGNORECASE)


class CardError(ValueError):
    """A file `load_card` refuses, with the reason readable as printed."""


def load_card(data: bytes, *, file_name: str = "") -> tuple[Card, list[str]]:
    """The card in `data` (a PNG or JSON file's bytes) and the notes the
    import should say out loud — a dropped lorebook, disagreeing embedded
    copies, macros nothing will bind. `file_name` (no path) rides the card
    as reference. Raises `CardError` when there is no card to have."""
    if data[: len(_PNG_MAGIC)] == _PNG_MAGIC:
        candidates = _png_candidates(data)
        if not candidates:
            raise CardError("no character card embedded in this PNG")
    else:
        candidates = [("json", _json_payload(data))]
    normalized = [(kw, payload, _normalize(payload)) for kw, payload in candidates]
    valid = [(kw, payload, card) for kw, payload, card in normalized if card is not None]
    if not valid:
        raise CardError("the card carries no character name")
    chosen = next(
        (entry for entry in valid if entry[0] == "ccv3"), valid[0]
    )  # the V3 spec: prefer ccv3; else the FIRST chara — never a dict's accident
    _, payload, card = chosen
    notes = []
    # Compared BEFORE the file name is stamped on: every candidate came
    # out of `_normalize` bare, and a stamped copy would "disagree" with
    # its own identical twin on the name alone.
    if any(other != card for _, _, other in valid):
        notes.append(
            f"the file embeds {len(valid)} cards that disagree; imported the {chosen[0]} one"
        )
    if file_name:
        card = _replace(card, file_name=file_name)
    book = _card_dict(payload).get("character_book")
    entries = book.get("entries") if isinstance(book, dict) else None
    if isinstance(entries, list) and entries:
        notes.append(f"dropped the card's lorebook ({len(entries)} entries) — not supported")
    unknown = _unknown_macros(card)
    if unknown:
        notes.append("left as written (nothing binds them): " + ", ".join(unknown))
    return card, notes


# ---------- reading the containers ----------


def _png_candidates(data: bytes) -> list[tuple[str, dict[str, object]]]:
    """Every embedded card, in file order, keyword-tagged — a list, never
    a dict: real PNGs carry duplicate keywords with differing payloads,
    and a dict would silently keep whichever assignment came last."""
    out: list[tuple[str, dict[str, object]]] = []
    pos = len(_PNG_MAGIC)
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


def _json_payload(data: bytes) -> dict[str, object]:
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CardError(f"not a PNG and not readable as JSON ({e})") from e
    if not isinstance(payload, dict):
        raise CardError("the JSON holds no card object")
    return payload


# ---------- normalizing one candidate ----------


def _normalize(payload: dict[str, object]) -> Card | None:
    """One candidate onto the allowlist; None when it has no name, which
    is the one thing a card cannot be without."""
    card = _card_dict(payload)
    name = _text(card, "name")
    if not name:
        return None
    return Card(
        name=name,
        description=_text(card, "description"),
        personality=_text(card, "personality"),
        scenario=_text(card, "scenario"),
        greeting=_text(card, "first_mes"),
        alternate_greetings=tuple(
            cleaned
            for item in _list(card, "alternate_greetings")
            if (cleaned := _newlines(str(item)).strip())
        ),
        examples=tuple(
            block
            for block in (b.strip() for b in _START.split(_text(card, "mes_example")))
            if block
        ),
        depth_note=_depth_note(card),
        system_prompt=_text(card, "system_prompt"),
        post_history_instructions=_text(card, "post_history_instructions"),
        creator_notes=_text(card, "creator_notes"),
    )


def _card_dict(payload: dict[str, object]) -> dict[str, object]:
    """The field dict itself: inside the V2/V3 `data` envelope when there
    is one, the payload as-is for V1's flat shape."""
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _depth_note(card: dict[str, object]) -> str:
    extensions = card.get("extensions")
    prompt = (extensions.get("depth_prompt") or {}) if isinstance(extensions, dict) else {}
    return _newlines(str(prompt.get("prompt", ""))).strip() if isinstance(prompt, dict) else ""


def _text(card: dict[str, object], key: str) -> str:
    value = card.get(key)
    return _newlines(value).strip() if isinstance(value, str) else ""


def _list(card: dict[str, object], key: str) -> list[object]:
    value = card.get(key)
    return value if isinstance(value, list) else []


def _newlines(text: str) -> str:
    """Line endings to `\\n`: cards mix CRLF and LF even within one file,
    and a carriage return cannot sit in a TOML literal block."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _unknown_macros(card: Card) -> list[str]:
    """Every `{{…}}` nothing will bind, deduplicated in reading order —
    reported at import rather than stripped or silently shipped."""
    text = "\n".join(
        [
            card.description,
            card.personality,
            card.scenario,
            card.greeting,
            card.depth_note,
            *card.alternate_greetings,
            *card.examples,
        ]
    )
    seen: dict[str, None] = {}
    for macro in _ANY_MACRO.findall(text):
        if not binds(macro):
            seen.setdefault(macro)
    return list(seen)
