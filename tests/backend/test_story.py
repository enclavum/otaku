"""A story's settings, one class each, tested from their contract.

Every setting answers the same questions — the switch, where it
injects (None for one that injects nothing), the positions it may take
(none for a flag), the story's own text (None for one with none) — so a
frontend draws them alike. A stored position off the closed list reads
as the default. `to_db` is the row to store, what is given laid over
the setting as it stands, and refuses what the setting does not take.
`injection` is what a switched-on setting sends: a tool's instruction
enclosed out of character in chat and bare in the system message, a
reminder as the user wrote it, nothing while off or empty.
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
from otaku.store.schema import StorySettingDB

PROMPTS = replace(Prompts(), ask_instruction="ASK HOW", notes_instruction="NOTE HOW")
FROM_THE_END = (-1, -2, -3, -4, -5, -6, -7, -8)


class TestAsItStands:
    def test_a_story_that_stored_nothing_has_the_setting_off_at_its_default(self) -> None:
        assert not questions().enabled
        assert questions().injection_position == -1
        assert reminder().injection_position == -3
        assert reminder().text == ""

    def test_what_the_story_stored_stands(self) -> None:
        stored = StorySettingDB("allow_questions", enabled=True, position=-4)
        found = questions(stored)
        assert (found.enabled, found.injection_position) == (True, -4)
        assert reminder(StorySettingDB("use_story_reminder", text="Rain.")).text == "Rain."

    def test_a_position_the_setting_may_not_take_reads_as_its_default(self) -> None:
        assert questions(StorySettingDB("allow_questions", position=-9)).injection_position == -1
        assert (
            reminder(StorySettingDB("use_story_reminder", position="system")).injection_position
            == -3
        )

    def test_the_positions_are_a_closed_list(self) -> None:
        assert StorySettingQuestions.allowed_positions == ("system", *FROM_THE_END)
        assert StorySettingAssistantNotes.allowed_positions == ("system", *FROM_THE_END)
        assert StorySettingReminder.allowed_positions == FROM_THE_END
        assert StorySettingSharedReminder.allowed_positions == FROM_THE_END

    def test_a_flag_injects_nothing_and_has_no_text(self) -> None:
        flag = StorySettingMode(StorySettingDB("story_mode", enabled=True))
        assert flag.enabled
        assert flag.allowed_positions == ()
        assert flag.injection_position is None
        assert flag.text is None

    def test_only_the_story_reminder_has_a_text_of_its_own(self) -> None:
        assert questions().text is None
        assert shared("Stay grim.").text is None
        assert reminder().text == ""

    def test_every_setting_has_a_name_and_a_label(self) -> None:
        for cls in every_setting(StorySetting):
            if cls.__name__.startswith("StorySetting"):
                assert cls.name and cls.label, cls.__name__


class TestToDb:
    def test_nothing_given_is_the_row_as_it_stands(self) -> None:
        assert questions().to_db() == StorySettingDB("allow_questions", position=-1)
        assert reminder().to_db() == StorySettingDB("use_story_reminder", position=-3, text="")
        assert StorySettingMode().to_db() == StorySettingDB("story_mode")

    def test_what_is_given_is_laid_over_it(self) -> None:
        row = questions(StorySettingDB("allow_questions", position=-2)).to_db(enabled=True)
        assert row == StorySettingDB("allow_questions", enabled=True, position=-2)
        row = reminder().to_db(enabled=True, position=-4, text="Rain.")
        assert row == StorySettingDB("use_story_reminder", enabled=True, position=-4, text="Rain.")

    def test_a_position_outside_the_list_is_refused(self) -> None:
        for position in ("end", 0, -9, "-2", True):
            with pytest.raises(Refused):
                questions().to_db(position=position)  # type: ignore[arg-type]
        with pytest.raises(Refused):
            reminder().to_db(position="system")

    def test_a_flag_takes_no_position(self) -> None:
        with pytest.raises(Refused):
            StorySettingMode().to_db(position=-1)

    def test_only_the_story_reminder_takes_a_text(self) -> None:
        with pytest.raises(Refused):
            questions().to_db(text="Ask about the weather.")
        with pytest.raises(Refused):
            shared("Stay grim.").to_db(text="Stay grimmer.")
        with pytest.raises(Refused):
            StorySettingMode().to_db(text="x")


class TestInjection:
    def test_nothing_while_off(self) -> None:
        assert questions().injection is None
        assert reminder(StorySettingDB("use_story_reminder", text="Rain.")).injection is None

    def test_a_tool_in_chat_is_enclosed_out_of_character(self) -> None:
        found = questions(StorySettingDB("allow_questions", enabled=True)).injection
        assert found is not None
        assert (found.owner, found.text, found.position) == (
            "allow_questions",
            "((OOC: ASK HOW))",
            -1,
        )

    def test_a_tool_in_the_system_message_is_bare(self) -> None:
        found = notes(StorySettingDB("allow_assistant_notes", enabled=True, position="system"))
        assert found.injection is not None
        assert (found.injection.text, found.injection.position) == ("NOTE HOW", "system")

    def test_the_story_reminder_goes_as_the_user_wrote_it(self) -> None:
        stored = StorySettingDB(
            "use_story_reminder", enabled=True, position=-4, text="[Style: terse]"
        )
        found = reminder(stored).injection
        assert found is not None
        assert (found.text, found.position) == ("[Style: terse]", -4)

    def test_the_shared_reminder_sends_the_text_stories_share(self) -> None:
        found = shared(
            "Stay grim.\n", StorySettingDB("use_shared_reminder", enabled=True)
        ).injection
        assert found is not None
        assert (found.owner, found.text, found.position) == (
            "use_shared_reminder",
            "Stay grim.",
            -3,
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
