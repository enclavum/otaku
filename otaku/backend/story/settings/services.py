"""A story's settings: one class per switch, and the set a story has.

What a story STORES is the store's (`schema.StorySettingDB`: a name,
a flag, a position, a reminder, a display flag, a layout). What a
setting IS is declared here, one class each: a tool's, which lets the
model use it and injects its prompt; a reminder's, which injects
the user's own text — the SHARED one, written once and switched on by
each story that wants it, and the STORY's own; and the LAYOUT the
story is read in, a value rather than a switch.
`StorySettings` is every setting of one story as it stands, built from
the rows the story holds, the prompts file and the store, and what the
switched-on ones inject. A story's settings are operated here too
(`list_settings`, `get_setting`, `update_setting`).

A new setting is a subclass and a row in `StorySettings`.
"""

from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from otaku.backend.app.core.services import App
from otaku.backend.errors import NO_SUCH_STORY, Invalid, NotFound
from otaku.backend.story.tools import (
    Tool,
    ToolAssistantNote,
    ToolQuestion,
    ToolSystemInfo,
    ToolWebSearch,
)
from otaku.context.injections import Injection
from otaku.context.syntax import OOC_FRAME
from otaku.context.tools import ToolSet
from otaku.settings.prompts import Prompts
from otaku.settings.prompts import load as load_prompts
from otaku.store import Store
from otaku.store.schema import (
    CompletionType,
    InjectionPosition,
    Layout,
    StoryMode,
    StorySettingDB,
)

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
    # Every position it may take, as a frontend offers them; none here.
    allowed_positions: ClassVar[tuple[InjectionPosition, ...]] = ()

    enabled: bool
    injection_position: InjectionPosition | None  # None: it injects nothing
    reminder_text: str | None  # the story's own reminder; None: the setting has none
    display_notes: bool | None  # whether the reader sees the notes; None: nothing to display
    layout: Layout | None  # how the story is laid out; None: not this setting's to say

    def __init__(self, from_db: StorySettingDB | None = None) -> None:
        self.enabled = from_db is not None and from_db.enabled
        self.injection_position = None
        self.reminder_text = None
        self.display_notes = None
        self.layout = None

    def to_db(
        self,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        reminder_text: str | None = None,
        display_notes: bool | None = None,
        layout: Layout | None = None,
    ) -> StorySettingDB:
        """The row to store: the setting as it stands, what is given laid
        over it. Raises Refused for what the setting does not take — here
        a position, a `reminder_text`, `display_notes` and a `layout`; a
        subclass that takes one says so, and the shared reminder is
        `set_shared_reminder`'s."""
        if position is not None:
            raise Invalid(f"{self.label} has no position: it injects nothing.")
        if reminder_text is not None:
            raise Invalid(f"{self.label} has no reminder of its own.")
        if display_notes is not None:
            raise Invalid(f"{self.label} has nothing to display.")
        if layout is not None:
            raise Invalid(f"{self.label} has no layout.")
        return StorySettingDB(self.name, enabled=self.enabled if enabled is None else enabled)

    def standing(self, row: StorySettingDB) -> str:
        """Where the setting stands once `row` is stored, in words — the
        confirmation a write answers with."""
        if not row.enabled:
            return f"{self.label}: off."
        if row.position is None:
            return f"{self.label}: on."
        if row.position.depth is None:
            return f"{self.label}: on, in the system message."
        # before which of the reader's messages, counted from the end
        depth = row.position.depth
        if depth == 1:
            return f"{self.label}: on, before your latest message."
        if depth == 2:
            return f"{self.label}: on, before your previous message."
        suffix = "rd" if depth == 3 else "th"
        return f"{self.label}: on, before your {depth}{suffix}-last message."


class InjectingSetting(StorySetting):
    """A setting that injects a text: where it rides is the story's to
    say, within the closed list a frontend offers — a stored position
    off the list reads as the default. `injection_label` names the TEXT
    it injects, where `label` names the switch: the thing placed on a
    depth ruler, listed by `/context` — lowercase, as the backend states
    a name; a frontend capitalises where its medium wants."""

    injection_label: ClassVar[str] = ""
    # The prompts.toml key of the PROMPT it injects, otaku's text edited
    # in place (`story.prompts.services`); None where the text is the
    # reader's own, a reminder's.
    prompt_name: ClassVar[str | None] = None
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
        layout: Layout | None = None,
    ) -> StorySettingDB:
        if position is not None and position not in self.allowed_positions:
            allowed = ", ".join(str(each.value) for each in self.allowed_positions)
            raise Invalid(f"{self.label} takes one of these positions: {allowed}.")
        row = super().to_db(
            enabled=enabled,
            reminder_text=reminder_text,
            display_notes=display_notes,
            layout=layout,
        )
        return replace(row, position=self.injection_position if position is None else position)

    def _injection_text(self) -> str:
        """The text it injects, bare — the subclass's; `injection` frames it."""
        raise NotImplementedError


class ToolSetting(InjectingSetting):
    """A tool's switch: while on, the model may use the tool, and its
    prompt rides where the story put it — enclosed out of character in
    chat, bare in the system message. The prompt is read off the
    prompts under the setting's `prompt_name`."""

    tool: ClassVar[type[Tool]] = Tool  # narrowed: a tool's switch always has one
    prompt_name: ClassVar[str] = ""  # narrowed: a tool's switch always sends one
    # Five of the reader's messages back: just before the newest, a small
    # model took a tool's instruction for the task and wrote the call
    # alone, or nothing.
    default_position = InjectionPosition(5)
    allowed_positions = _TOOL_POSITIONS

    prompt: str

    def __init__(self, from_db: StorySettingDB | None, prompts: Prompts) -> None:
        super().__init__(from_db)
        self.prompt = getattr(prompts, self.prompt_name)

    def _injection_text(self) -> str:
        return self.prompt


class StorySettingQuestions(ToolSetting):
    name = "allow_questions"
    label = "Allow questions"
    injection_label = "questions"
    prompt_name = "tool_questions_prompt"
    tool = ToolQuestion


class StorySettingAssistantNotes(ToolSetting):
    """The notes tool's switch, and a second one: whether the reader sees
    the notes the model writes. What the model gets is the same either way."""

    name = "allow_assistant_notes"
    label = "Allow assistant notes"
    injection_label = "assistant notes"
    prompt_name = "tool_assistant_notes_prompt"
    tool = ToolAssistantNote

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
        layout: Layout | None = None,
    ) -> StorySettingDB:
        row = super().to_db(
            enabled=enabled, position=position, reminder_text=reminder_text, layout=layout
        )
        return replace(
            row, display_notes=self.display_notes if display_notes is None else display_notes
        )


class StorySettingSystemInfo(ToolSetting):
    """The system info tool's switch: the model may ask the time, the
    sky and which model it is — otaku runs it."""

    name = "allow_system_info"
    label = "Allow system info"
    injection_label = "system info"
    prompt_name = "tool_system_info_prompt"
    tool = ToolSystemInfo


class StorySettingWebSearch(ToolSetting):
    """The web search tool's switch: the model may look something up —
    otaku runs it, through the engine the web search key chose."""

    name = "allow_web_search"
    label = "Allow web search"
    injection_label = "web search"
    prompt_name = "tool_web_search_prompt"
    tool = ToolWebSearch


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
        layout: Layout | None = None,
    ) -> StorySettingDB:
        row = super().to_db(
            enabled=enabled, position=position, display_notes=display_notes, layout=layout
        )
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


class StorySettingEmulateTextCompletion(InjectingSetting):
    """The canvas over the chat wire told to behave as a text completion:
    the prompt (prompts.toml's `emulate_text_completion_prompt`) that
    makes an instruct model continue the one text where it ends rather
    than answer it. Off until a story switches it on, as every switch
    is, and gated by the layout and the wire: it injects where the story
    is one text over the chat wire — the canvas, and a novel on a model
    that cannot complete text, played as the canvas — the novel's model
    on the text wire continuing by nature; off that, the switch stands
    and sends nothing."""

    name = "emulate_text_completion"
    label = "Emulate text completion"
    injection_label = "text completion emulation"
    prompt_name: ClassVar[str] = "emulate_text_completion_prompt"
    # The system message: a standing instruction, not a reminder.
    default_position = InjectionPosition()
    allowed_positions = _TOOL_POSITIONS

    prompt: str

    def __init__(
        self,
        from_db: StorySettingDB | None,
        prompts: Prompts,
        *,
        layout: Layout,
        completion_type: CompletionType,
    ) -> None:
        super().__init__(from_db)
        self.prompt = getattr(prompts, self.prompt_name)
        self._applies = layout.mode is StoryMode.STORY and completion_type is CompletionType.CHAT

    @property
    def injection(self) -> Injection | None:
        return super().injection if self._applies else None

    def _injection_text(self) -> str:
        return self.prompt


class StorySettingLayout(StorySetting):
    """The layout the story is read in, and written — a value, not a
    switch: a story is always laid out one way, MANUSCRIPT until it says
    otherwise (`schema.Layout`), and each frontend draws its look in its
    own medium (the page's `shell.js`, the terminal's flow for the
    canvas). Off the Tools tabs: no caption there names it."""

    name = "layout"
    label = "Layout"
    default_layout: ClassVar[Layout] = Layout.MANUSCRIPT

    layout: Layout

    def __init__(self, from_db: StorySettingDB | None = None) -> None:
        super().__init__(from_db)
        self.enabled = True  # always in force: there is no reading in no layout
        stored = from_db.layout if from_db is not None else None
        # the codec reads only the four names; anything else is unsaid
        self.layout = stored if stored is not None else self.default_layout

    def to_db(
        self,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        reminder_text: str | None = None,
        display_notes: bool | None = None,
        layout: Layout | None = None,
    ) -> StorySettingDB:
        if enabled is not None:
            raise Invalid(f"{self.label} has no switch: a story is always read in one.")
        row = super().to_db(
            position=position, reminder_text=reminder_text, display_notes=display_notes
        )
        chosen = self.layout if layout is None else layout
        return replace(row, enabled=True, layout=chosen)

    def standing(self, row: StorySettingDB) -> str:
        # a caption, not a sentence: the switches' confirmations end in a
        # full stop, this one does not
        return f"{self.label}: {(row.layout or self.layout).value}"


class StorySettings:
    """Every setting of one story as it stands — the rows it stored laid
    over the defaults, a row this build does not know passed over — and
    what the switched-on ones inject. Reads the prompts file and the
    shared reminder itself, so a caller hands over only what it holds:
    `can_complete_text`, the model's, which with the layout decides the
    wire the emulation setting is gated on."""

    def __init__(
        self,
        from_db: Sequence[StorySettingDB],
        store: Store,
        prompts_file: Path,
        *,
        can_complete_text: bool,
    ) -> None:
        prompts, _ = load_prompts(prompts_file)  # its warnings are the launch's to show
        by_name = {row.name: row for row in from_db}
        layout = StorySettingLayout(by_name.get(StorySettingLayout.name))
        # In the order they are sent where they share a place: the shared
        # reminder, the story's, then the tools, then the emulation. A
        # frontend draws them in its own order (its caption table), never
        # this one.
        settings: list[StorySetting] = [
            StorySettingSharedReminder(
                by_name.get(StorySettingSharedReminder.name),
                store.settings.get_shared_reminder(),
            ),
            StorySettingReminder(by_name.get(StorySettingReminder.name)),
            StorySettingAssistantNotes(by_name.get(StorySettingAssistantNotes.name), prompts),
            StorySettingQuestions(by_name.get(StorySettingQuestions.name), prompts),
            StorySettingSystemInfo(by_name.get(StorySettingSystemInfo.name), prompts),
            StorySettingWebSearch(by_name.get(StorySettingWebSearch.name), prompts),
            StorySettingEmulateTextCompletion(
                by_name.get(StorySettingEmulateTextCompletion.name),
                prompts,
                layout=layout.layout,
                completion_type=layout.layout.completion_type(can_complete_text),
            ),
            layout,
        ]
        self._by_name = {setting.name: setting for setting in settings}

    def __iter__(self) -> Iterator[StorySetting]:
        return iter(self._by_name.values())

    def get(self, name: str) -> StorySetting | None:
        """The setting stored under `name`; None when this build has none."""
        return self._by_name.get(name)

    @property
    def layout(self) -> Layout:
        """The layout the story is read in — the setting's, the default
        until the story says otherwise (`StorySettingLayout`)."""
        setting = self._by_name[StorySettingLayout.name]
        assert isinstance(setting, StorySettingLayout)  # every story has one
        return setting.layout

    @property
    def tool_set(self) -> ToolSet:
        """The story's tools that are on — how their calls go on the wire:
        such a call is sent as written, and every other leaves (a tool
        switched off, or one this build no longer has)."""
        tools = [setting for setting in self if isinstance(setting, ToolSetting)]
        return ToolSet(on=frozenset(setting.tool.name for setting in tools if setting.enabled))

    @property
    def shown_tools(self) -> frozenset[str]:
        """The tools whose calls a reader is shown: a question while its
        tool is on — off, a tool does not exist for the model, and the
        reader sees what the model reads — and the notes while theirs is
        on with its display switch."""
        shown: set[str] = set()
        for setting in self:
            tool = setting.tool
            if tool is None or not setting.enabled:
                continue
            if tool.name == "question":
                shown.add("question")
            elif tool.name == "note" and setting.display_notes:
                shown.add("note")
        return frozenset(shown)

    @property
    def injections(self) -> tuple[Injection, ...]:
        """What the switched-on settings send, in the order above."""
        return tuple(
            injection
            for setting in self
            if isinstance(setting, InjectingSetting)
            and (injection := setting.injection) is not None
        )


# ---------- a story's settings, operated ----------


def settings_of(app: App, story_id: int) -> StorySettings:
    """A story's settings as they stand, read off the store, on the model
    in use."""
    return StorySettings(
        app._store.stories.get_settings(story_id),
        app._store,
        app._paths.prompts_file,
        can_complete_text=app._model_facts().can_complete_text,
    )


def list_settings(app: App, story_id: int) -> StorySettings:
    """A story's settings as they stand. Raises NotFound for a story
    that is not there."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    return settings_of(app, story_id)


def get_setting(app: App, story_id: int, name: str) -> StorySetting:
    """One setting of a story as it stands. Raises NotFound for a story
    or a setting that is not there."""
    held = list_settings(app, story_id)
    found = held.get(name)
    if found is None:
        known = ", ".join(each.name for each in held)
        raise NotFound(f"Unknown setting {name!r}. Settings: {known}.")
    return found


def update_setting(
    app: App,
    story_id: int,
    name: str,
    *,
    enabled: bool | None = None,
    position: InjectionPosition | None = None,
    reminder_text: str | None = None,
    display_notes: bool | None = None,
    layout: Layout | None = None,
) -> str:
    """Change one setting of a story; what is not given stays. Answers
    the setting's own confirmation (`StorySetting.standing`). Raises
    NotFound for a story or a setting that is not there, Invalid for
    what the setting does not take (`StorySetting.to_db`)."""
    found = get_setting(app, story_id, name)
    row = found.to_db(
        enabled=enabled,
        position=position,
        reminder_text=reminder_text,
        display_notes=display_notes,
        layout=layout,
    )
    app._store.stories.set_setting(story_id, row)
    return found.standing(row)
