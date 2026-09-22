"""A story's settings: one class per switch, and the set a story has.

What a story STORES is the store's (`schema.StorySettingDB`: a name,
a flag, a position, a text). What a
setting IS is declared here, one class each: a tool's, which lets the
model use it and injects its instruction; a reminder's, which injects
the user's own text — the SHARED one, written once and switched on by
each story that wants it, and the STORY's own; later a plain flag.
`StorySettings` is every setting of one story as it stands, built from
the rows the story holds, the prompts file and the store, and what the
switched-on ones inject. The operations are `backend.api.stories`'.

A new setting is a subclass and a row in `StorySettings`.
"""

from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from otaku.backend.errors import Refused
from otaku.backend.tools import Tool, ToolAssistantNotes, ToolQuestions
from otaku.context.injections import Injection
from otaku.context.syntax import OOC_FRAME
from otaku.settings.prompts import Prompts
from otaku.settings.prompts import load as load_prompts
from otaku.store import Store
from otaku.store.schema import InjectionPosition, StorySettingDB

# The deepest a story may place what a setting injects: deep enough for a
# reminder (2 to 4 up is where one holds in practice), and no deeper —
# every message below an injection is re-read each turn.
_DEEPEST_POSITION = -8
# The closed lists a frontend offers. A reminder has the numbers alone —
# it exists because a model forgets its system message — a tool has
# "system" too.
_REMINDER_POSITIONS: tuple[InjectionPosition, ...] = tuple(range(-1, _DEEPEST_POSITION - 1, -1))
_TOOL_POSITIONS: tuple[InjectionPosition, ...] = ("system", *_REMINDER_POSITIONS)


class StorySetting:
    """One switch of a story, declared by its class; an instance is how
    one story has it — from the row it stored, or off where it stored
    none. Every setting answers the same questions, so a frontend
    draws them alike: what it may take is None or empty where it takes
    nothing, and a subclass fills in what it does."""

    name: ClassVar[str] = ""  # what a story stores it under: a rename orphans stored settings
    label: ClassVar[str] = ""
    # Every position it may take, as a frontend offers them; none here:
    # a flag injects nothing.
    allowed_positions: ClassVar[tuple[InjectionPosition, ...]] = ()

    enabled: bool
    injection_position: InjectionPosition | None  # None: it injects nothing
    text: str | None  # the story's own text; None: it has none of its own

    def __init__(self, from_db: StorySettingDB | None = None) -> None:
        self.enabled = from_db is not None and from_db.enabled
        self.injection_position = None
        self.text = None

    def to_db(
        self,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        text: str | None = None,
    ) -> StorySettingDB:
        """The row to store: the setting as it stands, what is given laid
        over it. Raises Refused for what the setting does not take — here
        a position and a text; a subclass that takes one says so, and
        the shared reminder's text is `set_shared_reminder`'s."""
        if position is not None:
            raise Refused(f"{self.label} has no position: it injects nothing.")
        if text is not None:
            raise Refused(f"{self.label} has no text of its own in a story.")
        return StorySettingDB(self.name, enabled=self.enabled if enabled is None else enabled)


class InjectingSetting(StorySetting):
    """A setting that injects a text: where it rides is the story's to
    say, within the closed list a frontend offers — a stored position
    off the list reads as the default."""

    default_position: ClassVar[InjectionPosition] = -1

    injection_position: InjectionPosition

    def __init__(self, from_db: StorySettingDB | None = None) -> None:
        super().__init__(from_db)
        stored = from_db.position if from_db is not None else None
        self.injection_position = (
            stored if stored in self.allowed_positions else self.default_position
        )

    @property
    def injection(self) -> Injection | None:
        """What it sends; None while off, or with nothing to send."""
        text = self._text().strip() if self.enabled else ""
        return Injection(self.name, text, self.injection_position) if text else None

    def to_db(
        self,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        text: str | None = None,
    ) -> StorySettingDB:
        # By type first: to Python True is 1 and -1.0 is -1, and neither
        # is a position.
        if position is not None and (
            type(position) not in (int, str) or position not in self.allowed_positions
        ):
            allowed = ", ".join(str(place) for place in self.allowed_positions)
            raise Refused(f"{self.label} takes one of these positions: {allowed}.")
        row = super().to_db(enabled=enabled, text=text)
        return replace(row, position=self.injection_position if position is None else position)

    def _text(self) -> str:
        """The text as it goes on the wire — the subclass's."""
        raise NotImplementedError


class ToolSetting(InjectingSetting):
    """A tool's switch: while on, the model may use the tool, and its
    instruction rides where the story put it — enclosed out of character
    in chat, bare in the system message. The instruction is read off the
    prompts under the tool's `prompt_name`."""

    tool: ClassVar[type[Tool]] = Tool
    # Above the newest message: all the recency, and nothing to re-read.
    default_position = -1
    allowed_positions = _TOOL_POSITIONS

    prompt: str

    def __init__(self, from_db: StorySettingDB | None, prompts: Prompts) -> None:
        super().__init__(from_db)
        self.prompt = getattr(prompts, self.tool.prompt_name)

    def _text(self) -> str:
        if self.injection_position == "system":
            return self.prompt
        return OOC_FRAME.replace("{body}", self.prompt.strip())


class StorySettingQuestions(ToolSetting):
    name = "allow_questions"
    label = "Questions"
    tool = ToolQuestions


class StorySettingAssistantNotes(ToolSetting):
    name = "allow_assistant_notes"
    label = "Assistant notes"
    tool = ToolAssistantNotes


class StorySettingReminder(InjectingSetting):
    """The story's own reminder: its text is the row's, sent verbatim."""

    name = "use_story_reminder"
    label = "Story reminder"
    # Deeper than an instruction: a reminder is to be kept in mind, not
    # obeyed at once.
    default_position = -3
    allowed_positions = _REMINDER_POSITIONS

    text: str

    def __init__(self, from_db: StorySettingDB | None = None) -> None:
        super().__init__(from_db)
        self.text = from_db.text if from_db is not None else ""

    def to_db(
        self,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        text: str | None = None,
    ) -> StorySettingDB:
        row = super().to_db(enabled=enabled, position=position)
        return replace(row, text=self.text if text is None else text)

    def _text(self) -> str:
        return self.text


class StorySettingSharedReminder(InjectingSetting):
    """The reminder stories share: a story switches it on or off and has
    no say in its text, kept once in the store's `settings` and sent
    verbatim."""

    name = "use_shared_reminder"
    label = "Shared reminder"
    default_position = -3
    allowed_positions = _REMINDER_POSITIONS

    shared_text: str  # not the story's own (`text` stays None): every story's

    def __init__(self, from_db: StorySettingDB | None, shared_text: str) -> None:
        super().__init__(from_db)
        self.shared_text = shared_text

    def _text(self) -> str:
        return self.shared_text


class StorySettingMode(StorySetting):
    """Story mode: the story sent as one text for the model to continue,
    over a text-completion wire — a flag, injecting nothing. Not built
    yet: nothing reads it."""

    name = "story_mode"
    label = "Story mode"


class StorySettings:
    """Every setting of one story as it stands — the rows it stored laid
    over the defaults, a row this build does not know passed over — and
    what the switched-on ones inject. Reads the prompts file and the
    shared reminder itself, so a caller hands over only what it holds."""

    def __init__(self, from_db: Sequence[StorySettingDB], store: Store, prompts_file: Path) -> None:
        prompts, _ = load_prompts(prompts_file)  # its warnings are the launch's to show
        by_name = {row.name: row for row in from_db}
        # In the order they are sent where they share a place.
        settings: list[StorySetting] = [
            StorySettingSharedReminder(
                by_name.get(StorySettingSharedReminder.name),
                store.settings.get_shared_reminder(),
            ),
            StorySettingReminder(by_name.get(StorySettingReminder.name)),
            StorySettingAssistantNotes(by_name.get(StorySettingAssistantNotes.name), prompts),
            StorySettingQuestions(by_name.get(StorySettingQuestions.name), prompts),
            StorySettingMode(by_name.get(StorySettingMode.name)),
        ]
        self._by_name = {setting.name: setting for setting in settings}

    def __iter__(self) -> Iterator[StorySetting]:
        return iter(self._by_name.values())

    def get(self, name: str) -> StorySetting | None:
        """The setting stored under `name`; None when this build has none."""
        return self._by_name.get(name)

    @property
    def injections(self) -> tuple[Injection, ...]:
        """What the switched-on settings send, in the order above."""
        return tuple(
            injection
            for setting in self
            if isinstance(setting, InjectingSetting)
            and (injection := setting.injection) is not None
        )
