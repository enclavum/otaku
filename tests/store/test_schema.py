"""The `attachments` column's text: a JSON list for a turn with
pictures, NULL for one without — never an empty list pretending to be
absent — and the same rows back. The keys are a contract with the SQL
that reads the column (`$.file` is what the sweep extracts).

The `settings` column's text: one JSON object, a key per setting.
`StorySettingDB.from_json` reads every setting it holds as the column records
it — a switch, a position, a text — and what it cannot make sense of as
unsaid; it knows no setting by name. `StorySettingDB.to_json` puts
settings into the text and keeps every key it does not know.
"""

import json
from dataclasses import replace

from otaku.store.schema import Attachment, InjectionPosition, StorySettingDB

CAT = Attachment(file="pic-0001-20260918-a3f9c1e2.jpg", width=1568, height=1043, size=312044)
DOG = Attachment(file="pic-0001-20260918-7b02d4ee.png", width=800, height=600, size=90210)
ASKING = StorySettingDB("allow_questions", enabled=True)


class TestAttachmentsColumn:
    def test_a_turn_without_pictures_is_null(self) -> None:
        assert Attachment.to_json(()) is None

    def test_null_reads_as_no_pictures(self) -> None:
        assert Attachment.from_json(None) == ()
        assert Attachment.from_json("") == ()

    def test_pictures_round_trip_in_order(self) -> None:
        assert Attachment.from_json(Attachment.to_json((CAT, DOG))) == (CAT, DOG)

    def test_the_text_names_the_file_under_the_key_the_sweep_reads(self) -> None:
        (row,) = json.loads(Attachment.to_json((CAT,)) or "")
        assert row["file"] == CAT.file
        assert set(row) == {"file", "width", "height", "size"}


class TestInjectionPosition:
    """`InjectionPosition` — where an injected text rides: the system
    message (no depth) or before the reader's Nth-last message. Its
    plain form is what the column and the wire hold; `text` names it as
    the depth ruler does."""

    def test_the_plain_form_is_system_or_the_depth(self) -> None:
        assert InjectionPosition().value == "system"
        assert InjectionPosition(1).value == 1
        assert InjectionPosition(8).value == 8

    def test_the_plain_form_reads_back(self) -> None:
        for position in (InjectionPosition(), *(InjectionPosition(d) for d in range(1, 9))):
            assert InjectionPosition.from_value(position.value) == position

    def test_what_is_not_a_position_reads_as_none(self) -> None:
        # By type first: to Python True is 1 and 1.0 is 1, and neither is a position.
        for value in (0, -1, True, 1.0, "3", "end", None, [1], {"depth": 1}):
            assert InjectionPosition.from_value(value) is None, value

    def test_named_as_the_depth_ruler_names_it(self) -> None:
        # The reader's newest message is the last; before it is "2nd last".
        assert InjectionPosition().text == "system"
        assert [InjectionPosition(d).text for d in (1, 2, 3, 4, 8)] == [
            "2nd last",
            "3rd last",
            "4th last",
            "5th last",
            "9th last",
        ]

    def test_equal_by_value(self) -> None:
        assert InjectionPosition(2) == InjectionPosition(2)
        assert InjectionPosition() == InjectionPosition()
        assert InjectionPosition(2) != InjectionPosition(3) != InjectionPosition()
        assert len({InjectionPosition(2), InjectionPosition(2), InjectionPosition()}) == 2


class TestSettingsColumn:
    def test_a_story_without_settings_has_none(self) -> None:
        assert StorySettingDB.from_json("") == ()

    def test_every_setting_is_read_as_the_column_records_it(self) -> None:
        raw = json.dumps(
            {
                "allow_questions": {"enabled": True, "position": "system"},
                "use_story_reminder": {
                    "enabled": True,
                    "position": 5,
                    "reminder_text": "Stay grim.",
                },
                "allow_assistant_notes": {"display_notes": False},
            }
        )
        assert StorySettingDB.from_json(raw) == (
            StorySettingDB("allow_questions", enabled=True, position=InjectionPosition()),
            StorySettingDB(
                "use_story_reminder",
                enabled=True,
                position=InjectionPosition(5),
                reminder_text="Stay grim.",
            ),
            StorySettingDB("allow_assistant_notes", display_notes=False),
        )

    def test_a_setting_it_never_heard_of_is_read_all_the_same(self) -> None:
        # Which settings exist is no business of the column's.
        assert StorySettingDB.from_json('{"story_mode": {"enabled": true}}') == (
            StorySettingDB("story_mode", enabled=True),
        )

    def test_what_makes_no_sense_reads_as_unsaid(self) -> None:
        # By type first: to Python True is 1 and 1.0 is 1, and neither is a position.
        for said in ("end", "2", 0, -3, True, 1.0, None):
            raw = json.dumps({"allow_questions": {"position": said}})
            assert StorySettingDB.from_json(raw) == (StorySettingDB("allow_questions"),), said
        for said in ("yes", 1, None):
            raw = json.dumps({"allow_questions": {"enabled": said}})
            assert StorySettingDB.from_json(raw) == (StorySettingDB("allow_questions"),), said
        # A display flag that is not `false` reads as displayed, the default.
        for said in ("no", 0, None):
            raw = json.dumps({"allow_assistant_notes": {"display_notes": said}})
            found = StorySettingDB.from_json(raw)
            assert found == (StorySettingDB("allow_assistant_notes"),), said
            assert found[0].display_notes is True

    def test_text_that_is_not_an_object_of_objects_holds_no_settings(self) -> None:
        for raw in ("not json", "[1, 2]", '"text"', '{"allow_questions": "on"}'):
            assert StorySettingDB.from_json(raw) == ()

    def test_a_written_setting_reads_back(self) -> None:
        setting = StorySettingDB(
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(4),
            reminder_text="Stay grim.",
        )
        assert StorySettingDB.from_json(StorySettingDB.to_json([setting], "")) == (setting,)
        hidden = StorySettingDB("allow_assistant_notes", enabled=True, display_notes=False)
        assert StorySettingDB.from_json(StorySettingDB.to_json([hidden], "")) == (hidden,)

    def test_every_key_it_does_not_know_is_kept(self) -> None:
        # A JSON key bumps no schema version: an older build can meet a
        # newer one's settings, and must hand them back whole.
        raw = json.dumps({"story_mode": {"enabled": True}, "allow_questions": {"tone": "dry"}})
        written = json.loads(StorySettingDB.to_json([ASKING], raw))
        assert written == {
            "story_mode": {"enabled": True},
            "allow_questions": {"tone": "dry", "enabled": True},
        }

    def test_only_what_is_said_is_written(self) -> None:
        # No position, no reminder, the notes displayed: the defaults are
        # not written, so only the exception ever is.
        assert json.loads(StorySettingDB.to_json([ASKING])) == {
            "allow_questions": {"enabled": True}
        }
        hidden = StorySettingDB("allow_assistant_notes", display_notes=False)
        assert json.loads(StorySettingDB.to_json([hidden])) == {
            "allow_assistant_notes": {"enabled": False, "display_notes": False}
        }

    def test_several_settings_are_written_at_once(self) -> None:
        reminding = StorySettingDB(
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(3),
            reminder_text="Stay grim.",
        )
        assert StorySettingDB.from_json(StorySettingDB.to_json([ASKING, reminding])) == (
            ASKING,
            reminding,
        )

    def test_an_emptied_reminder_leaves_the_column(self) -> None:
        reminding = StorySettingDB("use_story_reminder", enabled=True, reminder_text="Stay grim.")
        raw = StorySettingDB.to_json([reminding])
        cleared = json.loads(StorySettingDB.to_json([replace(reminding, reminder_text="")], raw))
        assert cleared == {"use_story_reminder": {"enabled": True}}

    def test_notes_displayed_again_leave_the_flag_out(self) -> None:
        hidden = StorySettingDB("allow_assistant_notes", enabled=True, display_notes=False)
        raw = StorySettingDB.to_json([hidden])
        shown = json.loads(StorySettingDB.to_json([replace(hidden, display_notes=True)], raw))
        assert shown == {"allow_assistant_notes": {"enabled": True}}

    def test_over_garbage_it_starts_clean(self) -> None:
        written = json.loads(StorySettingDB.to_json([ASKING], "not json"))
        assert written == {"allow_questions": {"enabled": True}}
