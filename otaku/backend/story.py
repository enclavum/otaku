"""A story's settings: one class per switch, and the set a story has.

What a story STORES is the store's (`schema.StorySettingDB`: a name,
a flag, a position, a reminder, a display flag). What a
setting IS is declared here, one class each: a tool's, which lets the
model use it and injects its prompt; a reminder's, which injects
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
from otaku.context.tool_calls import ToolSet
from otaku.settings.prompts import Prompts
from otaku.settings.prompts import load as load_prompts
from otaku.store import Store
from otaku.store.schema import InjectionPosition, StorySettingDB

# The deepest a story may place what a setting injects — before which of
# the reader's messages, counted from the end: deep enough for a reminder
# (2 to 4 back is where one holds in practice), and no deeper — every
# message below an injection is re-read each turn.
_DEEPEST_POSITION = 8
# The closed lists a frontend offers. A reminder has the numbers alone —
# it exists because a model forgets its system message — a tool has
# "system" too.
_REMINDER_POSITIONS: tuple[InjectionPosition, ...] = tuple(
    InjectionPosition(depth) for depth in range(1, _DEEPEST_POSITION + 1)
)
_TOOL_POSITIONS: tuple[InjectionPosition, ...] = (InjectionPosition(), *_REMINDER_POSITIONS)


class StorySetting:
    """One switch of a story, declared by its class; an instance is how
    one story has it — from the row it stored, or off where it stored
    none. Every setting answers the same questions, so a frontend
    draws them alike: what it may take is None or empty where it takes
    nothing, and a subclass fills in what it does."""

    name: ClassVar[str] = ""  # what a story stores it under: a rename orphans stored settings
    label: ClassVar[str] = ""
    tool: ClassVar[type[Tool] | None] = None  # the tool it switches, if one
    # Every position it may take, as a frontend offers them; none here:
    # a flag injects nothing.
    allowed_positions: ClassVar[tuple[InjectionPosition, ...]] = ()

    enabled: bool
    injection_position: InjectionPosition | None  # None: it injects nothing
    reminder_text: str | None  # the story's own reminder; None: the setting has none
    display_notes: bool | None  # whether the reader sees the notes; None: nothing to display

    def __init__(self, from_db: StorySettingDB | None = None) -> None:
        self.enabled = from_db is not None and from_db.enabled
        self.injection_position = None
        self.reminder_text = None
        self.display_notes = None

    def to_db(
        self,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        reminder_text: str | None = None,
        display_notes: bool | None = None,
    ) -> StorySettingDB:
        """The row to store: the setting as it stands, what is given laid
        over it. Raises Refused for what the setting does not take — here
        a position, a `reminder_text` and `display_notes`; a subclass that
        takes one says so, and the shared reminder is `set_shared_reminder`'s."""
        if position is not None:
            raise Refused(f"{self.label} has no position: it injects nothing.")
        if reminder_text is not None:
            raise Refused(f"{self.label} has no reminder of its own.")
        if display_notes is not None:
            raise Refused(f"{self.label} has nothing to display.")
        return StorySettingDB(self.name, enabled=self.enabled if enabled is None else enabled)


class InjectingSetting(StorySetting):
    """A setting that injects a text: where it rides is the story's to
    say, within the closed list a frontend offers — a stored position
    off the list reads as the default. `injection_label` names the TEXT
    it injects, where `label` names the switch: the thing placed on a
    depth ruler, listed by `/context` — lowercase, as the backend states
    a name; a frontend capitalises where its medium wants."""

    injection_label: ClassVar[str] = ""
    default_position: ClassVar[InjectionPosition] = InjectionPosition(1)

    injection_position: InjectionPosition

    def __init__(self, from_db: StorySettingDB | None = None) -> None:
        super().__init__(from_db)
        stored = from_db.position if from_db is not None else None
        self.injection_position = (
            stored if stored in self.allowed_positions else self.default_position
        )

    @property
    def injection(self) -> Injection | None:
        """What it sends; None while off, or with nothing to send. In chat
        the text is enclosed out of character — it rides a message of the
        reader's, where bare text would read as the reader's line, and
        every text otaku injects into the chat is out of character; the
        system message takes it bare."""
        body = self._injection_text().strip() if self.enabled else ""
        if not body:
            return None
        system = self.injection_position.depth is None
        text = body if system else OOC_FRAME.replace("{body}", body)
        return Injection(self.name, text, self.injection_position)

    def to_db(
        self,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        reminder_text: str | None = None,
        display_notes: bool | None = None,
    ) -> StorySettingDB:
        if position is not None and position not in self.allowed_positions:
            allowed = ", ".join(str(each.value) for each in self.allowed_positions)
            raise Refused(f"{self.label} takes one of these positions: {allowed}.")
        row = super().to_db(
            enabled=enabled, reminder_text=reminder_text, display_notes=display_notes
        )
        return replace(row, position=self.injection_position if position is None else position)

    def _injection_text(self) -> str:
        """The text it injects, bare — the subclass's; `injection` frames it."""
        raise NotImplementedError


class ToolSetting(InjectingSetting):
    """A tool's switch: while on, the model may use the tool, and its
    prompt rides where the story put it — enclosed out of character in
    chat, bare in the system message. The prompt is read off the
    prompts under the tool's `prompt_name`."""

    tool: ClassVar[type[Tool]] = Tool  # narrowed: a tool's switch always has one
    # Before the reader's newest message: all the recency, and nothing to
    # re-read.
    default_position = InjectionPosition(1)
    allowed_positions = _TOOL_POSITIONS

    prompt: str

    def __init__(self, from_db: StorySettingDB | None, prompts: Prompts) -> None:
        super().__init__(from_db)
        self.prompt = getattr(prompts, self.tool.prompt_name)

    def _injection_text(self) -> str:
        return self.prompt


class StorySettingQuestions(ToolSetting):
    name = "allow_questions"
    label = "Allow questions"
    injection_label = "questions"
    tool = ToolQuestions


class StorySettingAssistantNotes(ToolSetting):
    """The notes tool's switch, and a second one: whether the reader sees
    the notes the model writes. What the model gets is the same either way."""

    name = "allow_assistant_notes"
    label = "Allow assistant notes"
    injection_label = "assistant notes"
    tool = ToolAssistantNotes

    display_notes: bool

    def __init__(self, from_db: StorySettingDB | None, prompts: Prompts) -> None:
        super().__init__(from_db, prompts)
        self.display_notes = from_db.display_notes if from_db is not None else True

    def to_db(
        self,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        reminder_text: str | None = None,
        display_notes: bool | None = None,
    ) -> StorySettingDB:
        row = super().to_db(enabled=enabled, position=position, reminder_text=reminder_text)
        return replace(
            row, display_notes=self.display_notes if display_notes is None else display_notes
        )


class StorySettingReminder(InjectingSetting):
    """The story's own reminder: the row's, enclosed out of character —
    it rides a message of the reader's, where bare text would read as
    the reader's line, and a reminder is meta by nature."""

    name = "use_story_reminder"
    label = "Set story reminder"
    injection_label = "story reminder"
    # Deeper than a tool's prompt — before the reader's previous message: a
    # reminder is to be kept in mind, not obeyed at once.
    default_position = InjectionPosition(2)
    allowed_positions = _REMINDER_POSITIONS

    reminder_text: str

    def __init__(self, from_db: StorySettingDB | None = None) -> None:
        super().__init__(from_db)
        self.reminder_text = from_db.reminder_text if from_db is not None else ""

    def to_db(
        self,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        reminder_text: str | None = None,
        display_notes: bool | None = None,
    ) -> StorySettingDB:
        row = super().to_db(enabled=enabled, position=position, display_notes=display_notes)
        return replace(
            row, reminder_text=self.reminder_text if reminder_text is None else reminder_text
        )

    def _injection_text(self) -> str:
        return self.reminder_text


class StorySettingSharedReminder(InjectingSetting):
    """The reminder stories share: a story switches it on or off and has
    no say in its words, kept once in the store's `settings` and enclosed
    out of character as the story's own is."""

    name = "use_shared_reminder"
    label = "Use shared reminder"
    injection_label = "shared reminder"
    default_position = InjectionPosition(2)
    allowed_positions = _REMINDER_POSITIONS

    shared_reminder_text: str  # not the story's own (`reminder_text` stays None)

    def __init__(self, from_db: StorySettingDB | None, shared_reminder_text: str) -> None:
        super().__init__(from_db)
        self.shared_reminder_text = shared_reminder_text

    def _injection_text(self) -> str:
        return self.shared_reminder_text


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
    def tool_set(self) -> ToolSet:
        """The story's tools, each on or off — how their calls go on the
        wire: a tool that is on sends its calls as written, one that is
        off sends its `to_prose`; every other name (a tool this build no
        longer has) is left out."""
        tools = [setting for setting in self if isinstance(setting, ToolSetting)]
        return ToolSet(
            on=frozenset(setting.tool.name for setting in tools if setting.enabled),
            off={
                setting.tool.name: setting.tool.to_prose for setting in tools if not setting.enabled
            },
        )

    @property
    def injections(self) -> tuple[Injection, ...]:
        """What the switched-on settings send, in the order above."""
        return tuple(
            injection
            for setting in self
            if isinstance(setting, InjectingSetting)
            and (injection := setting.injection) is not None
        )
