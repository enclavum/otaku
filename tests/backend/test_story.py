"""A story's settings, one class each, tested from their contract.

Every setting answers the same questions — the switch, where it
injects (None for one that injects nothing), the positions it may take
(none for a value), the story's own reminder (None for every setting but
the story reminder's), whether the notes are displayed (None for every
setting but the notes tool's), the layout the story is read in
(None for every setting but the layout's) — so a frontend draws
them alike. A stored position off the closed list reads as the default.
`to_db` is the row to store, what is given laid over the setting as it
stands, and refuses what the setting does not take. `injection` is what
a switched-on setting sends: a tool's prompt and a reminder alike
enclosed out of character in chat and bare in the system message,
nothing while off or empty. The emulation setting is off until a story
switches it on, as every switch is, and gated: it injects in story mode
over the chat wire alone.
"""

from dataclasses import replace

import pytest

from otaku.backend.errors import Refused
from otaku.backend.story import (
    StorySetting,
    StorySettingAssistantNotes,
    StorySettingEmulateTextCompletion,
    StorySettingLayout,
    StorySettingQuestions,
    StorySettingReminder,
    StorySettingSharedReminder,
)
from otaku.settings.prompts import Prompts
from otaku.store.schema import CompletionType, InjectionPosition, Layout, StorySettingDB

PROMPTS = replace(
    Prompts(),
    tool_questions_prompt="ASK HOW",
    tool_assistant_notes_prompt="NOTE HOW",
    emulate_text_completion_prompt="CONTINUE HOW",
)
FROM_THE_END = tuple(InjectionPosition(depth) for depth in range(1, 9))
SYSTEM = InjectionPosition()
EMULATION = "emulate_text_completion"
EMULATING = StorySettingDB(EMULATION, enabled=True)


class TestAsItStands:
    def test_a_story_that_stored_nothing_has_the_setting_off_at_its_default(self) -> None:
        assert not questions().enabled
        assert questions().injection_position == InjectionPosition(1)
        assert reminder().injection_position == InjectionPosition(2)
        assert reminder().reminder_text == ""
        assert notes().display_notes is True

    def test_the_emulation_starts_off_at_the_system_message_as_every_switch_starts_off(
        self,
    ) -> None:
        assert not emulation().enabled
        assert emulation().injection_position == SYSTEM
        assert emulation(EMULATING).enabled
        for setting in (questions(), notes(), reminder(), shared("Stay grim.")):
            assert not setting.enabled, setting.name

    def test_what_the_story_stored_stands(self) -> None:
        stored = StorySettingDB("allow_questions", enabled=True, position=InjectionPosition(4))
        found = questions(stored)
        assert (found.enabled, found.injection_position) == (True, InjectionPosition(4))
        stored = StorySettingDB("use_story_reminder", reminder_text="Rain.")
        assert reminder(stored).reminder_text == "Rain."
        stored = StorySettingDB("allow_assistant_notes", display_notes=False)
        assert notes(stored).display_notes is False
        stored = StorySettingDB(EMULATION, enabled=True, position=InjectionPosition(3))
        assert emulation(stored).injection_position == InjectionPosition(3)

    def test_a_position_the_setting_may_not_take_reads_as_its_default(self) -> None:
        assert questions(
            StorySettingDB("allow_questions", position=InjectionPosition(9))
        ).injection_position == InjectionPosition(1)
        stored = StorySettingDB("use_story_reminder", position=SYSTEM)
        assert reminder(stored).injection_position == InjectionPosition(2)
        stored = StorySettingDB(EMULATION, position=InjectionPosition(9))
        assert emulation(stored).injection_position == SYSTEM

    def test_the_positions_are_a_closed_list(self) -> None:
        assert StorySettingQuestions.allowed_positions == (SYSTEM, *FROM_THE_END)
        assert StorySettingAssistantNotes.allowed_positions == (SYSTEM, *FROM_THE_END)
        assert StorySettingEmulateTextCompletion.allowed_positions == (SYSTEM, *FROM_THE_END)
        assert StorySettingReminder.allowed_positions == FROM_THE_END
        assert StorySettingSharedReminder.allowed_positions == FROM_THE_END
        assert StorySettingLayout.allowed_positions == ()

    def test_only_the_story_reminder_has_a_reminder_of_its_own(self) -> None:
        assert questions().reminder_text is None
        assert shared("Stay grim.").reminder_text is None
        assert emulation().reminder_text is None
        assert reminder().reminder_text == ""

    def test_only_the_notes_tool_has_something_to_display(self) -> None:
        assert questions().display_notes is None
        assert reminder().display_notes is None
        assert emulation().display_notes is None
        assert notes().display_notes is True

    def test_only_the_layout_setting_has_a_layout(self) -> None:
        for setting in (questions(), notes(), reminder(), shared("Stay grim."), emulation()):
            assert setting.layout is None, setting.name
        assert layout().layout is Layout.MANUSCRIPT

    def test_a_tool_setting_names_its_tool(self) -> None:
        assert StorySettingQuestions.tool is not None
        assert StorySettingQuestions.tool.name == "question"
        assert StorySettingAssistantNotes.tool is not None
        assert StorySettingAssistantNotes.tool.name == "note"
        assert StorySettingReminder.tool is None
        assert StorySettingEmulateTextCompletion.tool is None

    def test_every_setting_has_a_name_and_a_label(self) -> None:
        for cls in every_setting(StorySetting):
            if cls.__name__.startswith("StorySetting"):
                assert cls.name and cls.label, cls.__name__

    def test_the_emulation_reads_its_prompt_off_the_prompts(self) -> None:
        # the key is a Prompts field, so an edit in prompts.toml reaches it
        assert StorySettingEmulateTextCompletion.prompt_name == "emulate_text_completion_prompt"
        assert emulation().prompt == "CONTINUE HOW"


class TestToDb:
    def test_nothing_given_is_the_row_as_it_stands(self) -> None:
        assert questions().to_db() == StorySettingDB(
            "allow_questions", position=InjectionPosition(1)
        )
        assert reminder().to_db() == StorySettingDB(
            "use_story_reminder", position=InjectionPosition(2), reminder_text=""
        )
        assert notes().to_db() == StorySettingDB(
            "allow_assistant_notes", position=InjectionPosition(1), display_notes=True
        )
        assert emulation().to_db() == StorySettingDB(EMULATION, position=SYSTEM)

    def test_what_is_given_is_laid_over_it(self) -> None:
        row = questions(StorySettingDB("allow_questions", position=InjectionPosition(2))).to_db(
            enabled=True
        )
        assert row == StorySettingDB("allow_questions", enabled=True, position=InjectionPosition(2))
        row = reminder().to_db(enabled=True, position=InjectionPosition(4), reminder_text="Rain.")
        assert row == StorySettingDB(
            "use_story_reminder", enabled=True, position=InjectionPosition(4), reminder_text="Rain."
        )
        row = notes().to_db(display_notes=False)
        assert row == StorySettingDB(
            "allow_assistant_notes", position=InjectionPosition(1), display_notes=False
        )
        row = emulation().to_db(enabled=False, position=InjectionPosition(1))
        assert row == StorySettingDB(EMULATION, enabled=False, position=InjectionPosition(1))

    def test_a_position_outside_the_list_is_refused(self) -> None:
        # Off the list: past the deepest place, or the system message for
        # a reminder. (What is not a position at all never becomes one:
        # `InjectionPosition.from_value` refuses it at the boundary.)
        with pytest.raises(Refused):
            questions().to_db(position=InjectionPosition(9))
        with pytest.raises(Refused):
            reminder().to_db(position=SYSTEM)
        with pytest.raises(Refused):
            emulation().to_db(position=InjectionPosition(9))

    def test_only_the_story_reminder_takes_a_reminder(self) -> None:
        with pytest.raises(Refused):
            questions().to_db(reminder_text="Ask about the weather.")
        with pytest.raises(Refused):
            shared("Stay grim.").to_db(reminder_text="Stay grimmer.")
        with pytest.raises(Refused):
            emulation().to_db(reminder_text="Go on.")

    def test_only_the_notes_tool_takes_display_notes(self) -> None:
        with pytest.raises(Refused):
            questions().to_db(display_notes=False)
        with pytest.raises(Refused):
            reminder().to_db(display_notes=True)
        with pytest.raises(Refused):
            emulation().to_db(display_notes=False)


class TestLayoutSetting:
    def test_a_story_that_stored_nothing_is_read_in_the_manuscript(self) -> None:
        found = StorySettingLayout()
        assert (found.enabled, found.layout) == (True, Layout.MANUSCRIPT)
        # a value, not a switch: it injects nothing and displays nothing
        assert found.allowed_positions == ()
        assert (found.injection_position, found.reminder_text, found.display_notes) == (
            None,
            None,
            None,
        )

    def test_what_the_story_stored_stands_and_is_always_in_force(self) -> None:
        stored = StorySettingDB("layout", enabled=False, layout=Layout.CHAT)
        found = StorySettingLayout(stored)
        assert (found.enabled, found.layout) == (True, Layout.CHAT)
        # a row that names no layout reads as the default
        assert layout(StorySettingDB("layout")).layout is Layout.MANUSCRIPT

    def test_the_row_to_store_carries_the_layout_given_or_standing(self) -> None:
        assert StorySettingLayout().to_db() == StorySettingDB(
            "layout", enabled=True, layout=Layout.MANUSCRIPT
        )
        stored = StorySettingDB("layout", layout=Layout.CHAT)
        assert layout(stored).to_db(layout=Layout.NOVEL) == StorySettingDB(
            "layout", enabled=True, layout=Layout.NOVEL
        )

    def test_it_has_no_switch_and_takes_nothing_else(self) -> None:
        for asked in (
            {"enabled": True},
            {"enabled": False},
            {"position": InjectionPosition(1)},
            {"reminder_text": "Rain."},
            {"display_notes": False},
        ):
            with pytest.raises(Refused):
                StorySettingLayout().to_db(**asked)  # type: ignore[arg-type]

    def test_a_switch_takes_no_layout(self) -> None:
        for setting in (questions(), reminder(), notes(), shared("Stay grim."), emulation()):
            with pytest.raises(Refused):
                setting.to_db(layout=Layout.CANVAS)


class TestInjection:
    def test_nothing_while_off(self) -> None:
        assert questions().injection is None
        stored = StorySettingDB("use_story_reminder", reminder_text="Rain.")
        assert reminder(stored).injection is None
        assert emulation().injection is None

    def test_a_tool_in_chat_is_enclosed_out_of_character(self) -> None:
        found = questions(StorySettingDB("allow_questions", enabled=True)).injection
        assert found is not None
        assert (found.owner, found.text, found.position) == (
            "allow_questions",
            "((OOC: ASK HOW))",
            InjectionPosition(1),
        )

    def test_a_tool_in_the_system_message_is_bare(self) -> None:
        found = notes(StorySettingDB("allow_assistant_notes", enabled=True, position=SYSTEM))
        assert found.injection is not None
        assert (found.injection.text, found.injection.position) == ("NOTE HOW", SYSTEM)

    def test_the_story_reminder_is_enclosed_out_of_character(self) -> None:
        stored = StorySettingDB(
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(4),
            reminder_text="[Style: terse]\n",
        )
        found = reminder(stored).injection
        assert found is not None
        assert (found.text, found.position) == ("((OOC: [Style: terse]))", InjectionPosition(4))

    def test_the_shared_reminder_sends_the_text_stories_share(self) -> None:
        found = shared(
            "Stay grim.\n", StorySettingDB("use_shared_reminder", enabled=True)
        ).injection
        assert found is not None
        assert (found.owner, found.text, found.position) == (
            "use_shared_reminder",
            "((OOC: Stay grim.))",
            InjectionPosition(2),
        )

    def test_a_reminder_with_nothing_to_say_is_not_sent(self) -> None:
        assert reminder(StorySettingDB("use_story_reminder", enabled=True)).injection is None
        assert shared("  ", StorySettingDB("use_shared_reminder", enabled=True)).injection is None

    def test_the_emulation_sends_its_prompt_in_story_mode_over_the_chat_wire(self) -> None:
        found = emulation(EMULATING).injection
        assert found is not None
        # bare at its default place, the system message
        assert (found.owner, found.text, found.position) == (EMULATION, "CONTINUE HOW", SYSTEM)
        # enclosed out of character at a depth, as every text in the chat is
        deeper = emulation(StorySettingDB(EMULATION, enabled=True, position=InjectionPosition(2)))
        assert deeper.injection is not None
        assert deeper.injection.text == "((OOC: CONTINUE HOW))"

    def test_the_emulation_is_gated_by_the_layout(self) -> None:
        # the novel's model continues by nature; no chat-mode layout is one text
        assert (
            emulation(EMULATING, layout=Layout.NOVEL, completion_type=CompletionType.TEXT).injection
            is None
        )
        assert emulation(EMULATING, layout=Layout.MANUSCRIPT).injection is None
        assert emulation(EMULATING, layout=Layout.CHAT).injection is None
        # gated, the switch still stands
        assert emulation(EMULATING, layout=Layout.CHAT).enabled
        # a novel on a model that cannot complete text is played as a canvas
        assert emulation(EMULATING, layout=Layout.NOVEL).injection is not None


def questions(from_db: StorySettingDB | None = None) -> StorySettingQuestions:
    return StorySettingQuestions(from_db, PROMPTS)


def notes(from_db: StorySettingDB | None = None) -> StorySettingAssistantNotes:
    return StorySettingAssistantNotes(from_db, PROMPTS)


def reminder(from_db: StorySettingDB | None = None) -> StorySettingReminder:
    return StorySettingReminder(from_db)


def shared(text: str, from_db: StorySettingDB | None = None) -> StorySettingSharedReminder:
    return StorySettingSharedReminder(from_db, text)


def layout(from_db: StorySettingDB | None = None) -> StorySettingLayout:
    return StorySettingLayout(from_db)


def emulation(
    from_db: StorySettingDB | None = None,
    *,
    layout: Layout = Layout.CANVAS,
    completion_type: CompletionType = CompletionType.CHAT,
) -> StorySettingEmulateTextCompletion:
    """The emulation setting as one story has it — on the canvas over
    the chat wire unless the test says otherwise."""
    return StorySettingEmulateTextCompletion(
        from_db, PROMPTS, layout=layout, completion_type=completion_type
    )


def every_setting(base: type[StorySetting]) -> list[type[StorySetting]]:
    """Every class below `base`, however deep."""
    found: list[type[StorySetting]] = []
    for sub in base.__subclasses__():
        found += [sub, *every_setting(sub)]
    return found
