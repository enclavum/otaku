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
from SillyTavern's count of every message into otaku's of the reader's."""

import json
import re

from otaku.context.cards import CharacterProfile, binds

# SillyTavern's depth for a depth prompt that names none.
_ST_DEPTH = 4
_ANY_MACRO = re.compile(r"\{\{[^{}]{1,40}\}\}")
_START = re.compile(r"<START>", re.IGNORECASE)


class CardError(ValueError):
    """A file `load_card` refuses, with the reason readable as printed."""


def json_payload(data: bytes) -> dict[str, object]:
    """The card's text from a JSON file's bytes, a byte-order mark before
    it or not."""
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CardError(f"not a PNG and not readable as JSON ({e})") from e
    if not isinstance(payload, dict):
        raise CardError("the JSON holds no card object")
    return payload


def normalize(payload: dict[str, object]) -> CharacterProfile | None:
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


def has_lorebook(payload: dict[str, object]) -> bool:
    """Whether the card carries a lorebook with entries in it."""
    book = _card_dict(payload).get("character_book")
    entries = book.get("entries") if isinstance(book, dict) else None
    return isinstance(entries, list) and bool(entries)


def assets(payload: dict[str, object]) -> list[object]:
    """The assets the card lists (V3), as written; none for an older
    card."""
    return _list(_card_dict(payload), "assets")


def unknown_macros(card: CharacterProfile) -> list[str]:
    """Every `{{…}}` nothing will bind, deduplicated in reading order —
    reported at import rather than stripped or silently shipped."""
    text = "\n".join(
        [
            card.description,
            card.personality,
            card.scenario,
            card.greeting,
            card.reminder,
            card.system_prompt,
            *card.alternate_greetings,
            *card.examples,
        ]
    )
    seen: dict[str, None] = {}
    for macro in _ANY_MACRO.findall(text):
        if not binds(macro):
            seen.setdefault(macro)
    return list(seen)


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
