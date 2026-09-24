"""A story's settings, one class each, tested from their contract.

Every setting answers the same questions — the switch, where it
injects (None for one that injects nothing), the positions it may take
(none for a flag), the story's own reminder (None for every setting but
the story reminder's), whether the notes are displayed (None for every
setting but the notes tool's) — so a frontend draws them alike. A stored
position off the closed list reads as the default. `to_db` is the row
to store, what is given laid over the setting as it stands, and refuses
what the setting does not take. `injection` is what a switched-on
setting sends: a tool's prompt and a reminder alike enclosed out of
character in chat and bare in the system message, nothing while off or
empty.
"""

from dataclasses import replace

import pytest

from otaku.backend.errors import Refused
from otaku.backend.story import (
    StorySetting,
    StorySettingAssistantNotes,
    StorySettingMode,
    StorySettingQuestions,
    StorySettingReminder,
    StorySettingSharedReminder,
)
from otaku.settings.prompts import Prompts
from otaku.store.schema import InjectionPosition, StorySettingDB

PROMPTS = replace(
    Prompts(), tool_questions_prompt="ASK HOW", tool_assistant_notes_prompt="NOTE HOW"
)
FROM_THE_END = tuple(InjectionPosition(depth) for depth in range(1, 9))
SYSTEM = InjectionPosition()


class TestAsItStands:
    def test_a_story_that_stored_nothing_has_the_setting_off_at_its_default(self) -> None:
        assert not questions().enabled
        assert questions().injection_position == InjectionPosition(1)
        assert reminder().injection_position == InjectionPosition(2)
        assert reminder().reminder_text == ""
        assert notes().display_notes is True

    def test_what_the_story_stored_stands(self) -> None:
        stored = StorySettingDB("allow_questions", enabled=True, position=InjectionPosition(4))
        found = questions(stored)
        assert (found.enabled, found.injection_position) == (True, InjectionPosition(4))
        stored = StorySettingDB("use_story_reminder", reminder_text="Rain.")
        assert reminder(stored).reminder_text == "Rain."
        stored = StorySettingDB("allow_assistant_notes", display_notes=False)
        assert notes(stored).display_notes is False

    def test_a_position_the_setting_may_not_take_reads_as_its_default(self) -> None:
        assert questions(
            StorySettingDB("allow_questions", position=InjectionPosition(9))
        ).injection_position == InjectionPosition(1)
        stored = StorySettingDB("use_story_reminder", position=SYSTEM)
        assert reminder(stored).injection_position == InjectionPosition(2)

    def test_the_positions_are_a_closed_list(self) -> None:
        assert StorySettingQuestions.allowed_positions == (SYSTEM, *FROM_THE_END)
        assert StorySettingAssistantNotes.allowed_positions == (SYSTEM, *FROM_THE_END)
        assert StorySettingReminder.allowed_positions == FROM_THE_END
        assert StorySettingSharedReminder.allowed_positions == FROM_THE_END

    def test_a_flag_injects_nothing_and_has_no_reminder(self) -> None:
        flag = StorySettingMode(StorySettingDB("story_mode", enabled=True))
        assert flag.enabled
        assert flag.allowed_positions == ()
        assert flag.injection_position is None
        assert flag.reminder_text is None
        assert flag.display_notes is None

    def test_only_the_story_reminder_has_a_reminder_of_its_own(self) -> None:
        assert questions().reminder_text is None
        assert shared("Stay grim.").reminder_text is None
        assert reminder().reminder_text == ""

    def test_only_the_notes_tool_has_something_to_display(self) -> None:
        assert questions().display_notes is None
        assert reminder().display_notes is None
        assert notes().display_notes is True

    def test_a_tool_setting_names_its_tool(self) -> None:
        assert StorySettingQuestions.tool is not None
        assert StorySettingQuestions.tool.name == "question"
        assert StorySettingAssistantNotes.tool is not None
        assert StorySettingAssistantNotes.tool.name == "note"
        assert StorySettingReminder.tool is None

    def test_every_setting_has_a_name_and_a_label(self) -> None:
        for cls in every_setting(StorySetting):
            if cls.__name__.startswith("StorySetting"):
                assert cls.name and cls.label, cls.__name__


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
        assert StorySettingMode().to_db() == StorySettingDB("story_mode")

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

    def test_a_position_outside_the_list_is_refused(self) -> None:
        # Off the list: past the deepest place, or the system message for
        # a reminder. (What is not a position at all never becomes one:
        # `InjectionPosition.from_value` refuses it at the boundary.)
        with pytest.raises(Refused):
            questions().to_db(position=InjectionPosition(9))
        with pytest.raises(Refused):
            reminder().to_db(position=SYSTEM)

    def test_a_flag_takes_no_position(self) -> None:
        with pytest.raises(Refused):
            StorySettingMode().to_db(position=InjectionPosition(1))

    def test_only_the_story_reminder_takes_a_reminder(self) -> None:
        with pytest.raises(Refused):
            questions().to_db(reminder_text="Ask about the weather.")
        with pytest.raises(Refused):
            shared("Stay grim.").to_db(reminder_text="Stay grimmer.")
        with pytest.raises(Refused):
            StorySettingMode().to_db(reminder_text="x")

    def test_only_the_notes_tool_takes_display_notes(self) -> None:
        with pytest.raises(Refused):
            questions().to_db(display_notes=False)
        with pytest.raises(Refused):
            reminder().to_db(display_notes=True)
        with pytest.raises(Refused):
            StorySettingMode().to_db(display_notes=False)


class TestInjection:
    def test_nothing_while_off(self) -> None:
        assert questions().injection is None
        stored = StorySettingDB("use_story_reminder", reminder_text="Rain.")
        assert reminder(stored).injection is None

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


def questions(from_db: StorySettingDB | None = None) -> StorySettingQuestions:
    return StorySettingQuestions(from_db, PROMPTS)


def notes(from_db: StorySettingDB | None = None) -> StorySettingAssistantNotes:
    return StorySettingAssistantNotes(from_db, PROMPTS)


def reminder(from_db: StorySettingDB | None = None) -> StorySettingReminder:
    return StorySettingReminder(from_db)


def shared(text: str, from_db: StorySettingDB | None = None) -> StorySettingSharedReminder:
    return StorySettingSharedReminder(from_db, text)


def every_setting(base: type[StorySetting]) -> list[type[StorySetting]]:
    """Every class below `base`, however deep."""
    found: list[type[StorySetting]] = []
    for sub in base.__subclasses__():
        found += [sub, *every_setting(sub)]
    return found
