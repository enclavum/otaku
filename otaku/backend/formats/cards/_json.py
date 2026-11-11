"""A card's text — the card spec's JSON, V1 flat or V2/V3 under its
`data` envelope, whichever file carries it — read onto the one honest
`context.cards.CharacterProfile`; and the parts of it an import names
without reading them (its lorebook, its assets).

Normalization is an ALLOWLIST: the fields below are read, everything
else drops on the floor — the format leaks runtime junk (`chat`,
`char_persona`) and a denylist can never keep up. `{{char}}`/`{{user}}`
macros stay UNBOUND here: binding needs the chosen character name and
the player's, which only the import command knows. Line endings are
normalized to `\\n` — cards mix them, even within one file — and
`mes_example` is split on its `<START>` delimiter into separate example
blocks, because passed through whole the model reads the delimiter as
literal text. The depth prompt is the card's reminder, its depth turned
from SillyTavern's count of every message into otaku's of the reader's.

On export (`overlay`, `write_text`), a card is its text with otaku's
fields laid over it — each where it differs from what the text reads as,
so a field otaku never changed keeps its exact words — and the tagline,
which no card spec has a place for, under the card's `extensions.otaku`.
Everything else the text holds passes through as it came."""

import copy
import json
import re
from dataclasses import dataclass, fields

from otaku.context.cards import CharacterProfile

# SillyTavern's depth for a depth prompt that names none.
_ST_DEPTH = 4
# The specs a card's text is written in, as they name themselves.
_V2 = {"spec": "chara_card_v2", "spec_version": "2.0"}
_V3 = {"spec": "chara_card_v3", "spec_version": "3.0"}
# Where otaku keeps what no card spec has a place for.
_OTAKU = "otaku"
# A profile's fields as a card's text names them, the text fields alike;
# the lists, the reminder and its depth have writers of their own.
_TEXTS = {
    "name": "name",
    "description": "description",
    "personality": "personality",
    "scenario": "scenario",
    "greeting": "first_mes",
    "system_prompt": "system_prompt",
    "post_history_instructions": "post_history_instructions",
    "creator_notes": "creator_notes",
}
_START = re.compile(r"<START>", re.IGNORECASE)


class CardError(ValueError):
    """A file `read_card` refuses, with the reason readable as printed."""


@dataclass(frozen=True)
class Asset:
    """One of a card's pictures as the V3 spec lists a card's files: the
    type the card gives it (`icon`, `emotion`, `background`, … as the
    card wrote it), its name there ("" for none), and its bytes. Read,
    one beside the main picture — a CHARX's member, or a chunk RisuAI
    keeps in a PNG; written, any of a card's, the main one first."""

    type: str
    name: str
    data: bytes


# ---------- import ----------


def read_text(data: bytes) -> dict[str, object]:
    """The card's text from a JSON file's bytes, a byte-order mark before
    it or not."""
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CardError(f"not a PNG and not readable as JSON ({e})") from e
    if not isinstance(payload, dict):
        raise CardError("the JSON holds no card object")
    return payload


def read_profile(payload: dict[str, object]) -> CharacterProfile | None:
    """One candidate onto the allowlist; None when it has no name, which
    is the one thing a card cannot be without."""
    card = _card_dict(payload)
    name = _text(card, "name")
    if not name:
        return None
    return CharacterProfile(
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
        reminder=_reminder(card),
        reminder_depth=_reminder_depth(card),
        system_prompt=_text(card, "system_prompt"),
        post_history_instructions=_text(card, "post_history_instructions"),
        creator_notes=_text(card, "creator_notes"),
    )


def read_tagline(payload: dict[str, object]) -> str:
    """The tagline otaku wrote into a card's text ("" for none)."""
    extensions = _card_dict(payload).get("extensions")
    own = extensions.get(_OTAKU) if isinstance(extensions, dict) else None
    value = own.get("tagline") if isinstance(own, dict) else None
    return value.strip() if isinstance(value, str) else ""


def has_lorebook(payload: dict[str, object]) -> bool:
    """Whether the card carries a lorebook with entries in it."""
    book = _card_dict(payload).get("character_book")
    entries = book.get("entries") if isinstance(book, dict) else None
    return isinstance(entries, list) and bool(entries)


def list_assets(payload: dict[str, object]) -> list[object]:
    """The assets the card lists (V3), as written; none for an older
    card."""
    return _list(_card_dict(payload), "assets")


def list_risu_assets(payload: dict[str, object]) -> list[dict[str, str]]:
    """The files RisuAI lists in a V2 card's own extension, as a V3 card
    lists its assets — its emotions (`emotion`), then its additional
    assets (`x-risu-asset`), each its name and the uri it is kept under;
    none for a card without them."""
    extensions = _card_dict(payload).get("extensions")
    risu = extensions.get("risuai") if isinstance(extensions, dict) else None
    if not isinstance(risu, dict):
        return []
    out = []
    for key, kind in (("emotions", "emotion"), ("additionalAssets", "x-risu-asset")):
        for entry in _list(risu, key):
            if isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[1], str):
                out.append({"type": kind, "name": str(entry[0]), "uri": entry[1]})
    return out


def _card_dict(payload: dict[str, object]) -> dict[str, object]:
    """The field dict itself: inside the V2/V3 `data` envelope when there
    is one, the payload as-is for V1's flat shape."""
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _depth_prompt(card: dict[str, object]) -> dict[str, object]:
    extensions = card.get("extensions")
    prompt = (extensions.get("depth_prompt") or {}) if isinstance(extensions, dict) else {}
    return prompt if isinstance(prompt, dict) else {}


def _reminder(card: dict[str, object]) -> str:
    return _newlines(str(_depth_prompt(card).get("prompt", ""))).strip()


def _reminder_depth(card: dict[str, object]) -> int:
    """SillyTavern counts every message from the end, 0 after the last;
    otaku counts the reader's, 1 before the newest, and never goes after
    it — so half its depth, rounded up, from 1. A card without one takes
    SillyTavern's default, 4."""
    depth = _depth_prompt(card).get("depth", _ST_DEPTH)
    if type(depth) is not int:
        depth = _ST_DEPTH
    return max(1, -(-depth // 2))


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


# ---------- export ----------


def overlay(payload: dict[str, object], profile: CharacterProfile, line: str) -> dict[str, object]:
    """A copy of a card's text with `profile` and the tagline `line` laid
    over it: each field written where it differs from what the text
    reads as (`read_profile`), the rest left exactly as it came."""
    out = copy.deepcopy(payload)
    card = _card_dict(out)
    read = read_profile(payload)
    changed = {
        field.name
        for field in fields(CharacterProfile)
        if read is None or getattr(read, field.name) != getattr(profile, field.name)
    }
    for key in changed & _TEXTS.keys():
        card[_TEXTS[key]] = getattr(profile, key)
    if "alternate_greetings" in changed:
        card["alternate_greetings"] = list(profile.alternate_greetings)
    if "examples" in changed:
        card["mes_example"] = "\n".join(f"<START>\n{block}" for block in profile.examples)
    if changed & {"reminder", "reminder_depth"}:
        # a depth otaku left is the card's own, never one turned back
        _write_reminder(card, profile, keep_depth="reminder_depth" not in changed)
    if read_tagline(payload) != line:
        _write_tagline(card, line)
    return out


def write_text(profile: CharacterProfile, line: str) -> dict[str, object]:
    """A card's text written whole from `profile` and the tagline `line`:
    a V2 card, the spec every reader reads."""
    card: dict[str, object] = {key: getattr(profile, field) for field, key in _TEXTS.items()}
    card["alternate_greetings"] = list(profile.alternate_greetings)
    card["mes_example"] = "\n".join(f"<START>\n{block}" for block in profile.examples)
    card["extensions"] = {}
    if profile.reminder:
        _write_reminder(card, profile)
    if line:
        _write_tagline(card, line)
    return {**_V2, "data": card}


def as_v2(payload: dict[str, object]) -> dict[str, object]:
    """A card's text under the V2 envelope: V1's flat fields wrapped, a
    V3 card's relabelled — V2 reads the same fields."""
    return {**payload, **_V2, "data": copy.deepcopy(_card_dict(payload))}


def as_v3(payload: dict[str, object]) -> dict[str, object]:
    """A card's text under the V3 envelope, as `as_v2` does."""
    return {**payload, **_V3, "data": copy.deepcopy(_card_dict(payload))}


def _write_reminder(
    card: dict[str, object], profile: CharacterProfile, *, keep_depth: bool = False
) -> None:
    """The reminder as SillyTavern's depth prompt, its depth turned back
    into SillyTavern's count of every message — twice otaku's — or, with
    `keep_depth`, the card's own; the role the card gave it kept."""
    extensions = card.get("extensions")
    if not isinstance(extensions, dict):
        extensions = card["extensions"] = {}
    prompt = extensions.get("depth_prompt")
    had = prompt if isinstance(prompt, dict) else {}
    depth = had.get("depth") if keep_depth and type(had.get("depth")) is int else None
    extensions["depth_prompt"] = {
        **had,
        "prompt": profile.reminder,
        "depth": depth if depth is not None else profile.reminder_depth * 2,
        "role": had.get("role", "system"),
    }


def _write_tagline(card: dict[str, object], line: str) -> None:
    extensions = card.get("extensions")
    if not isinstance(extensions, dict):
        extensions = card["extensions"] = {}
    own = extensions.get(_OTAKU)
    if not isinstance(own, dict):
        own = extensions[_OTAKU] = {}
    if line:
        own["tagline"] = line
    else:
        own.pop("tagline", None)
        if not own:
            del extensions[_OTAKU]
