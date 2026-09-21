"""A story's features: what each switch stands at, and the injections
that come of them.

A feature is one switch of a story: a tool (`context.tools` — what it
injects is its instruction, from the prompts) or a reminder, the user's
own text — the GLOBAL one, written once for every story and switched on
in each, and the STORY's own. Pure over plain data — the settings JSON
as the store hands it, the prompts, the global reminder's text — so the
session resolves its injections with no operation in between;
`api.features` is the operations.

The settings JSON is one object with a key per setting. This module
owns the keys it knows and KEEPS the ones it does not: a JSON key bumps
no schema version, so an older build can meet a newer one's settings.
"""

import json
from dataclasses import dataclass

from otaku.context.assembler import Injection, Position
from otaku.context.syntax import OOC_FRAME
from otaku.context.tools import TOOLS
from otaku.settings.prompts import Prompts

# The two features that are no tool. A story switches the global one on
# or off and has no say in its text, which is every story's.
USE_GLOBAL_REMINDER = "use_global_reminder"
USE_STORY_REMINDER = "use_story_reminder"
# A tool's feature is named on its class (`Tool.feature`); here, the
# tools by that name — the registry itself is keyed by tag.
_TOOL_BY_FEATURE = {tool.feature: tool for tool in TOOLS.values()}
# Every feature by name, in the order they are sent where they share a
# place: the reminders, then each tool as the registry has it. A tool is
# one by being registered; a feature that is no tool is a name added here.
# The names are what a story's settings are kept under: a rename orphans
# them.
ALL_FEATURES: tuple[str, ...] = (USE_GLOBAL_REMINDER, USE_STORY_REMINDER, *_TOOL_BY_FEATURE)
# The key the global reminder's TEXT is kept under in the store's `globals`.
GLOBALS_REMINDER_KEY = "reminder"

_REMINDER_LABELS = {
    USE_GLOBAL_REMINDER: "Global reminder",
    USE_STORY_REMINDER: "Story reminder",
}
# The deepest a story may place a feature: deep enough for a reminder (2
# to 4 up is where one holds in practice), and no deeper — every message
# below an injection is re-read each turn.
_DEEPEST_POSITION = -8
# What `Feature.allowed_positions` offers: closed lists, which a frontend
# shows as they stand. A reminder has the numbers alone — it exists
# because a model forgets its system message — a tool has "system" too.
_ALLOWED_REMINDER_POSITIONS: tuple[Position, ...] = tuple(range(-1, _DEEPEST_POSITION - 1, -1))
_ALLOWED_TOOL_POSITIONS: tuple[Position, ...] = ("system", *_ALLOWED_REMINDER_POSITIONS)
# Where a reminder rides until its story says otherwise; a tool's default
# is its class's (`Tool.default_position`). Deeper than an instruction: a
# reminder is to be kept in mind, not obeyed at once.
_DEFAULT_REMINDER_POSITION = -3


@dataclass(frozen=True)
class Feature:
    """One feature as it stands in a story."""

    name: str
    label: str
    on: bool
    position: Position  # an injection's: "system", or -1 … counted from the end
    # Every position it may take, as a frontend offers them. A reminder
    # has no "system": it exists because a model forgets its system
    # message, which is the premise's.
    allowed_positions: tuple[Position, ...]
    text: str = ""  # the story reminder's alone: the story's own words


def read(raw: str) -> tuple[Feature, ...]:
    """Every feature of `ALL_FEATURES`, in its order, as the settings stand.
    What the JSON does not say, or says in a way that makes no sense,
    reads as the default: off, the default place."""
    settings = _parsed(raw)
    found: list[Feature] = []
    position: Position
    allowed: tuple[Position, ...]
    for name in ALL_FEATURES:
        tool = _TOOL_BY_FEATURE.get(name)
        if tool is None:  # a reminder
            label, position = _REMINDER_LABELS[name], _DEFAULT_REMINDER_POSITION
            allowed = _ALLOWED_REMINDER_POSITIONS
        else:
            label, position = tool.label, tool.default_position
            allowed = _ALLOWED_TOOL_POSITIONS
        found.append(_feature(settings, name, label, position, allowed))
    return tuple(found)


def write(raw: str, feature: Feature) -> str:
    """The settings JSON with this feature's state in it, every key this
    module does not know kept as it was — the object's and the feature's."""
    settings = _parsed(raw)
    state = settings.get(feature.name)
    state = dict(state) if isinstance(state, dict) else {}
    state |= {"on": feature.on, "position": feature.position}
    if feature.name == USE_STORY_REMINDER:
        state["text"] = feature.text
    settings[feature.name] = state
    return json.dumps(settings, ensure_ascii=False)


def injections(
    features: tuple[Feature, ...], prompts: Prompts, global_reminder: str
) -> tuple[Injection, ...]:
    """What the switched-on features put in the context, in the order
    given. otaku frames its own texts and the user's go as written: an
    instruction is enclosed out of character in chat and bare in the
    system message; a reminder is verbatim. An empty one is left out."""
    out: list[Injection] = []
    for feature in features:
        if not feature.on:
            continue
        if feature.name == USE_GLOBAL_REMINDER:
            text = global_reminder.strip()
        elif feature.name == USE_STORY_REMINDER:
            text = feature.text.strip()
        else:
            text = getattr(prompts, _TOOL_BY_FEATURE[feature.name].instruction_field).strip()
            if text and feature.position != "system":
                text = OOC_FRAME.replace("{body}", text)
        if text:
            out.append(Injection(feature.name, text, feature.position))
    return tuple(out)


def _parsed(raw: str) -> dict[str, object]:
    """The settings as an object; anything else — nothing, garbage, a
    list — is none at all."""
    try:
        settings = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return settings if isinstance(settings, dict) else {}


def _feature(
    settings: dict[str, object],
    name: str,
    label: str,
    position: Position,
    allowed: tuple[Position, ...],
) -> Feature:
    """One feature read out of the settings, over the default given."""
    state = settings.get(name)
    state = state if isinstance(state, dict) else {}
    said = state.get("position")
    text = state.get("text") if name == USE_STORY_REMINDER else ""
    return Feature(
        name=name,
        label=label,
        on=state.get("on") is True,
        # By type first: to Python True is 1 and -1.0 is -1, and neither is a position.
        position=said if type(said) in (int, str) and said in allowed else position,
        allowed_positions=allowed,
        text=text if isinstance(text, str) else "",
    )
