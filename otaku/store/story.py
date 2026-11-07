"""A story's settings as its row's `settings` column records them
(`StorySettingDB`), the values a setting takes — an injection's place,
the layout — and the readings a layout derives: the mode a row is written
in (`messages.mode`) and the wire it is sent on."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Self


@dataclass(frozen=True)
class StorySettingDB:
    """One setting of a story, as the `settings` column records it: a
    switch, where what it injects rides, the story's own reminder,
    whether the reader is shown the notes the model writes, and the
    layout the story is read in — the last three each one setting's, the
    others every injecting setting's.
    WHICH settings there are, and what each may take, is no business of
    the column's (`backend.story.settings.services`)."""

    name: str
    enabled: bool = False
    position: "InjectionPosition | None" = None  # None: unsaid, the setting's default applies
    reminder_text: str = ""
    display_notes: bool = True
    layout: "Layout | None" = None  # None: unsaid, the setting's default applies

    @classmethod
    def from_json(cls, text: str) -> tuple[Self, ...]:
        """The column read back: every setting it holds, in its order.
        What makes no sense reads as unsaid — `enabled` that is not `true`
        is False, a position that is not "system" or a count from 1 is
        None, a layout that is none of the four names is None — and a
        text that is no JSON object of objects holds none."""
        out = []
        for name, state in cls._parse(text).items():
            if not isinstance(state, dict):
                continue
            reminder_text = state.get("reminder_text")
            out.append(
                cls(
                    name=name,
                    enabled=state.get("enabled") is True,
                    position=InjectionPosition.from_value(state.get("position")),
                    reminder_text=reminder_text if isinstance(reminder_text, str) else "",
                    display_notes=state.get("display_notes") is not False,
                    layout=Layout.from_value(state.get("layout")),
                )
            )
        return tuple(out)

    @classmethod
    def to_json(cls, settings: Sequence[Self], current: str = "") -> str:
        """The column's text with these settings in it — a MERGE over
        `current`, never a rewrite: a JSON key bumps no schema version, so
        an older build can meet a newer one's keys and must hand them back
        whole. Only what is said is written; an emptied reminder leaves
        the column."""
        column = cls._parse(current)
        for setting in settings:
            state = column.get(setting.name)
            state = dict(state) if isinstance(state, dict) else {}
            state["enabled"] = setting.enabled
            if setting.position is not None:
                state["position"] = setting.position.value
            if setting.reminder_text:
                state["reminder_text"] = setting.reminder_text
            else:
                state.pop("reminder_text", None)
            if setting.display_notes:
                state.pop("display_notes", None)  # the default; only the exception is said
            else:
                state["display_notes"] = False
            if setting.layout is not None:
                state["layout"] = setting.layout.value
            column[setting.name] = state
        return json.dumps(column, ensure_ascii=False)

    @staticmethod
    def _parse(text: str) -> dict[str, object]:
        """The column's text parsed: the JSON object it holds, or an empty
        dict for anything else — nothing, garbage, a list."""
        try:
            parsed = json.loads(text) if text else {}
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class InjectionPosition:
    """Where an injected text rides: before the reader's `depth`-th
    message from the end (1 the newest), or, with no depth, in the
    system message. `value` is the plain form the column and the wire
    hold — "system" or the number — and `from_value` reads one back;
    `text` names it as every frontend does ("system", "2nd last" for 1:
    the reader's newest message is the last; the page copies the rule
    in `tools.js placeName`)."""

    depth: int | None = None

    @property
    def value(self) -> str | int:
        return "system" if self.depth is None else self.depth

    @property
    def text(self) -> str:
        if self.depth is None:
            return "system"
        nth = self.depth + 1
        return f"{nth}{'nd' if nth == 2 else 'rd' if nth == 3 else 'th'} last"

    @classmethod
    def from_value(cls, value: object) -> Self | None:
        """ "system" or an int from 1 (by type: True is not one); else None."""
        if value == "system":
            return cls()
        return cls(value) if type(value) is int and value >= 1 else None


class Layout(Enum):
    """How a story is laid out — read, and for one value written: the
    same turns four ways, the choice the story's. NOVEL is the canvas
    over the TEXT wire, the model continuing the text itself; CANVAS one
    continuous text over the chat wire; MANUSCRIPT the played lines as
    interjections among the prose; CHAT the turns as alternating blocks.
    Two facts derive from it, and the frontends read the facts: the
    mode it is written in (`mode`) and the wire (`completion_type`).
    `value` is the plain form the column and the wire hold, and
    `from_value` reads one back."""

    NOVEL = "novel"
    CANVAS = "canvas"
    MANUSCRIPT = "manuscript"
    CHAT = "chat"

    @classmethod
    def from_value(cls, value: object) -> Self | None:
        """One of the four names; else None."""
        return next((each for each in cls if each.value == value), None)

    @property
    def mode(self) -> "StoryMode":
        """STORY for the canvas, one text, and the novel, which is drawn
        as the canvas; CHAT for the manuscript and the chat."""
        return StoryMode.STORY if self in (Layout.CANVAS, Layout.NOVEL) else StoryMode.CHAT

    def completion_type(self, can_complete_text: bool) -> "CompletionType":
        """The wire alone: TEXT for the novel where the model in use can
        complete text (`ModelFacts.can_complete_text`), CHAT for every other
        case — a novel on a model that cannot is played as a canvas."""
        if self is Layout.NOVEL and can_complete_text:
            return CompletionType.TEXT
        return CompletionType.CHAT


class StoryMode(Enum):
    """The mode a story is written in: STORY one text, the reader's lines
    and the model's replies running on into each other; CHAT turns, each
    a paragraph of its own. A layout has one (`Layout.mode`), and
    `messages.mode` holds the one a row was written in — how it joins
    the text is decided off that (`context.assembler.get_join_type`)."""

    STORY = "story"
    CHAT = "chat"

    @classmethod
    def from_value(cls, value: object) -> "StoryMode":
        """The stored word; CHAT for anything this build does not know."""
        return next((each for each in cls if each.value == value), cls.CHAT)


class CompletionType(Enum):
    """The wire a request goes over — CHAT the message list, TEXT the raw
    text."""

    CHAT = "chat"
    TEXT = "text"
