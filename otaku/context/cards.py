"""Character cards as context material: the normalized `Card`, the
archive the cast row keeps, macro binding, and the wire block composed
back out of the archive — writer and reader of the archive in ONE
place. FILE decoding (PNG chunks, JSON) lives with the other wild
formats, in `backend.domain._formats.cards`; this module never touches bytes.
`{{char}}`/`{{user}}` macros stay unbound until `bind` applies the names
only the import knows. Everything here is pure strings.
"""

import json
import re
import tomllib
from dataclasses import dataclass, fields

# The macro spellings the corpus actually uses: `{{char}}`/`{{user}}` in
# any case, and the TavernAI-era `<BOT>`/`<USER>`.
_CHAR_MACRO = re.compile(r"\{\{\s*char\s*\}\}|<BOT>", re.IGNORECASE)
_USER_MACRO = re.compile(r"\{\{\s*user\s*\}\}|<USER>", re.IGNORECASE)
# The card template's `{field}` holes (`card_to_wire`).
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Card:
    """One character card, normalized. `name` is the only required field;
    text fields keep their macros."""

    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    greeting: str = ""  # first_mes: the character's opening line
    alternate_greetings: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()  # mes_example, split on <START>
    depth_note: str = ""  # a standing behavioural rule
    # Stored for later, sent nowhere: the template decides the wire.
    system_prompt: str = ""
    post_history_instructions: str = ""
    creator_notes: str = ""
    file_name: str = ""  # the source file's name, reference only


def bind(text: str, *, char: str, user: str) -> str:
    """The name macros resolved — `{{char}}`/`<BOT>` to the character,
    `{{user}}`/`<USER>` to the player, case-insensitively. Anything else
    in braces is left as written."""
    return _USER_MACRO.sub(
        user.replace("\\", r"\\"), _CHAR_MACRO.sub(char.replace("\\", r"\\"), text)
    )


def binds(macro: str) -> bool:
    """Whether `bind` would resolve this exact `{{…}}` (or TavernAI-era)
    token — the vocabulary question `backend.domain._formats.cards` asks to
    report macros nothing will bind."""
    return bool(_CHAR_MACRO.fullmatch(macro) or _USER_MACRO.fullmatch(macro))


def card_toml(card: Card, *, user: str) -> str:
    """The archive the characters table keeps: the card as the
    user-readable TOML "the fields the prompt was built from" — `name`
    and `user` first (the bindings, recorded as data), then every
    non-empty field, macros intact, so a later feature can re-compose or
    re-bind. One blank line between fields, so the archive reads in the
    lore view. Round-trips through `tomllib` field for field. Edge-clean:
    no trailing newline, so the archive is exactly what a document
    round-trip (which trims body edges) gives back."""
    lines = [_toml_pair("name", card.name), _toml_pair("user", user)]
    for field in fields(Card):
        if field.name == "name":
            continue
        value = getattr(card, field.name)
        if isinstance(value, str) and value:
            lines.append(_toml_pair(field.name, value))
        elif isinstance(value, tuple) and value:
            items = ", ".join(json.dumps(item) for item in value)
            lines.append(f"{field.name} = [{items}]")
    return "\n\n".join(lines)


def card_to_wire(toml_text: str, template: str) -> str:
    """What a card row sends: the template's `{field}` placeholders
    filled from the archive, every text bound to its recorded
    `name`/`user` on the way — at WIRE time, so a correction to the
    archive reaches every future request. One pass, so braces inside
    card text are never re-read as placeholders; a template line whose
    placeholders ALL came up empty is dropped — a card without a
    scenario ships no dangling `Scenario:` label."""
    data = tomllib.loads(toml_text)
    char, user = str(data.get("name", "")), str(data.get("user", ""))
    # Every card field answers, empty when the archive omitted it — an
    # absent scenario must drop its template line, not survive as a
    # literal `{scenario}`. Only names outside the card's vocabulary are
    # left as written.
    values: dict[str, str] = {field.name: "" for field in fields(Card)}
    values["user"] = user
    for key, value in data.items():
        if isinstance(value, str):
            values[key] = bind(value, char=char, user=user)
        elif isinstance(value, list):
            joined = "\n\n".join(str(item) for item in value)
            values[key] = bind(joined, char=char, user=user)
    out = []
    for line in template.split("\n"):
        holes = _PLACEHOLDER.findall(line)
        known = [name for name in holes if name in values]
        if known and all(not values[name].strip() for name in known):
            continue
        out.append(_PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), line))
    return "\n".join(out)


def _toml_pair(key: str, value: str) -> str:
    """One `key = value` row, round-trip exact: a clean single line as a
    literal string, multi-line as a triple-single literal (the leading
    newline trims), anything a literal cannot hold escaped the JSON way —
    which is a valid TOML basic string."""
    if "\n" not in value and "'" not in value and not _has_control(value):
        return f"{key} = '{value}'"
    if "'''" not in value and not _has_control(value.replace("\n", "")):
        return f"{key} = '''\n{value}'''"
    return f"{key} = {json.dumps(value)}"


def _has_control(text: str) -> bool:
    return any(ord(ch) < 32 and ch not in "\n\t" for ch in text)
