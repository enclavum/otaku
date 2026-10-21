"""Character cards as context material: a card's `CharacterProfile`,
normalized, and its TOML — a codex card's, or a story character's copy,
which adds the persona it binds — written and read back in ONE place
(`to_toml`, `from_toml`), the name always the row's own; macro binding;
the wire block
composed back out of a story character's profile, and what a card
injects besides its block (`card_injections`). FILE decoding (PNG
chunks, JSON) lives with the other wild formats, in
`backend.formats.cards`; this module never touches bytes.
`{{char}}`/`{{user}}` macros stay unbound until `bind` applies the names
only the import knows. Everything here is pure, over strings.
"""

import json
import re
import tomllib
from dataclasses import dataclass, fields
from typing import Any, Self

from otaku.context.injections import Injection
from otaku.store.schema import InjectionPosition

# The macro spellings the corpus actually uses: `{{char}}`/`{{user}}` in
# any case, and the TavernAI-era `<BOT>`/`<USER>`.
_CHAR_MACRO = re.compile(r"\{\{\s*char\s*\}\}|<BOT>", re.IGNORECASE)
_USER_MACRO = re.compile(r"\{\{\s*user\s*\}\}|<USER>", re.IGNORECASE)
# SillyTavern's stand-in, in a card's system prompt, for the prompt the
# card's replaces: here a card's joins the story's instead, so it stands
# for nothing.
_ORIGINAL_MACRO = re.compile(r"\{\{\s*original\s*\}\}", re.IGNORECASE)
# The reminder's key as a copy of 0.7.0 and before holds it — read as
# `reminder`, and answering "" where a template still names it: the
# reminder rides on its own now.
_RETIRED_REMINDER = "depth_note"
# The card template's `{field}` holes (`card_to_wire`).
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Who made what a card brings into a story — its character, its row,
# its greeting — as `provider` records it (`schema`): the card, no model.
CARD_PROVIDER = "card"


@dataclass(frozen=True)
class CharacterProfile:
    """Who a character is, as a card says it: its name and the card's
    fields, normalized — what a codex card and a story character's copy
    each keep as TOML (their `profile` columns) — and, in a story's copy,
    who `{{user}}` binds to there. `name` is the only required field;
    text fields keep their macros."""

    name: str
    # The persona a story brought the card in with; "" in the codex, which
    # binds nobody: a story's, not the card's to say.
    user: str = ""
    description: str = ""
    personality: str = ""
    scenario: str = ""
    greeting: str = ""  # first_mes: the character's opening line
    alternate_greetings: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()  # mes_example, split on <START>
    # A standing behavioural rule, sent on its own before the reader's
    # `reminder_depth`-th message from the end (1 the newest).
    reminder: str = ""
    reminder_depth: int = 2
    system_prompt: str = ""  # joined to the story's system message
    # Stored for later, sent nowhere.
    post_history_instructions: str = ""
    creator_notes: str = ""
    file_name: str = ""  # the source file's name, reference only

    @classmethod
    def from_toml(cls, toml_text: str, *, name: str) -> Self:
        """The profile a TOML text holds — a codex card's or a story
        character's copy alike — under `name`, its row's: every key of the
        profile's vocabulary but the name read back, a list as a tuple,
        `depth_note` as the reminder 0.7.0 and before wrote; anything else
        (the `name` line a copy before 0.8.0 carries, a key a later feature
        added) left out. Raises `tomllib.TOMLDecodeError` for a text that
        is no TOML."""
        data = tomllib.loads(toml_text) if toml_text else {}
        values: dict[str, Any] = {}
        for field in fields(cls):
            value = data.get(field.name)
            if field.name == "reminder" and value is None:
                value = data.get(_RETIRED_REMINDER)
            if field.name == "name" or value is None:
                continue
            if isinstance(value, str):
                values[field.name] = value
            elif isinstance(value, list):
                values[field.name] = tuple(str(item) for item in value)
            elif isinstance(value, int) and not isinstance(value, bool):
                values[field.name] = value
        return cls(name=name, **values)

    def to_toml(self) -> str:
        """The profile as user-readable TOML, "the fields the prompt was
        built from": every non-empty field but the name, which its row
        keeps, macros intact, so a later feature can re-compose or re-bind
        — a story's `user` first where it binds one. One blank line between
        fields, so it reads in the lore view. Round-trips through
        `from_toml` field for field. Edge-clean: no trailing newline, so it
        is exactly what a document round-trip (which trims body edges)
        gives back."""
        lines = _field_pairs(self)
        if self.user:
            lines = [_toml_pair("user", self.user), *lines]
        return "\n\n".join(lines)


def bind(text: str, *, char: str, user: str) -> str:
    """The name macros resolved — `{{char}}`/`<BOT>` to the character,
    `{{user}}`/`<USER>` to the player, case-insensitively — and
    `{{original}}` to nothing. Anything else in braces is left as
    written."""
    named = _CHAR_MACRO.sub(char.replace("\\", r"\\"), _ORIGINAL_MACRO.sub("", text))
    return _USER_MACRO.sub(user.replace("\\", r"\\"), named)


def binds(macro: str) -> bool:
    """Whether `bind` would resolve this exact `{{…}}` (or TavernAI-era)
    token — the vocabulary question `backend.formats.cards` asks to
    report macros nothing will bind."""
    return any(pattern.fullmatch(macro) for pattern in (_CHAR_MACRO, _USER_MACRO, _ORIGINAL_MACRO))


def card_injections(toml_text: str, *, char: str) -> tuple[Injection, Injection]:
    """What a card asks of the request besides its own row, read from a
    story character's profile: its system prompt, joined to the story's
    system message, and its reminder, sent before the reader's
    `reminder_depth`-th message from the end — each bound to `char`, the
    character's name, and the profile's `user`, empty (so not sent) where
    the card has none. Raises `tomllib.TOMLDecodeError` for a text that
    is no TOML."""
    card = CharacterProfile.from_toml(toml_text, name=char)
    user = card.user
    return (
        Injection(
            f"{char}'s system prompt",
            bind(card.system_prompt, char=char, user=user).strip(),
            InjectionPosition(),
        ),
        Injection(
            f"{char}'s reminder",
            bind(card.reminder, char=char, user=user).strip(),
            InjectionPosition(max(1, card.reminder_depth)),
        ),
    )


def card_to_wire(toml_text: str, template: str, *, char: str) -> str:
    """What a card row sends: the template's `{field}` placeholders
    filled from the story character's profile, `{name}` with `char` — its
    name — and every text bound to it and to the profile's `user` on the
    way — at WIRE time, so a correction to the profile reaches every
    future request. One pass, so braces inside
    card text are never re-read as placeholders; a template line whose
    placeholders ALL came up empty is dropped — a card without a
    scenario ships no dangling `Scenario:` label."""
    data = tomllib.loads(toml_text)
    user = str(data.get("user", ""))
    # Every card field answers, empty when the profile omitted it — an
    # absent scenario must drop its template line, not survive as a
    # literal `{scenario}`. Only names outside the card's vocabulary are
    # left as written.
    values: dict[str, str] = {field.name: "" for field in fields(CharacterProfile)}
    values["user"] = user
    for key, value in data.items():
        if isinstance(value, str):
            values[key] = bind(value, char=char, user=user)
        elif isinstance(value, list):
            joined = "\n\n".join(str(item) for item in value)
            values[key] = bind(joined, char=char, user=user)
        elif isinstance(value, int) and not isinstance(value, bool):
            values[key] = str(value)
    values["name"] = char
    values[_RETIRED_REMINDER] = ""
    out = []
    for line in template.split("\n"):
        holes = _PLACEHOLDER.findall(line)
        known = [name for name in holes if name in values]
        if known and all(not values[name].strip() for name in known):
            continue
        out.append(_PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), line))
    return "\n".join(out)


def _field_pairs(profile: CharacterProfile) -> list[str]:
    """Every non-empty field but the two names as a TOML row, in the
    profile's order, macros intact — a number where it is not its
    default."""
    lines = []
    for field in fields(CharacterProfile):
        if field.name in ("name", "user"):
            continue
        value = getattr(profile, field.name)
        if isinstance(value, str) and value:
            lines.append(_toml_pair(field.name, value))
        elif isinstance(value, tuple) and value:
            items = ", ".join(json.dumps(item) for item in value)
            lines.append(f"{field.name} = [{items}]")
        elif isinstance(value, int) and value != field.default:
            lines.append(f"{field.name} = {value}")
    return lines


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
