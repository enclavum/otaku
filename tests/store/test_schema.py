"""The `attachments` column's text: a JSON list for a turn with
pictures, NULL for one without — never an empty list pretending to be
absent — and the same rows back. The keys are a contract with the SQL
that reads the column (`$.file` is what the sweep extracts).

The `settings` column's text: one JSON object, a key per setting.
`StorySettingDB.from_json` reads every setting it holds as the column records
it — a switch, a position, a text, a layout — and what it cannot
make sense of as unsaid; it knows no setting by name.
`StorySettingDB.to_json` puts settings into the text and keeps every key
it does not know. `Layout` is the four readings of a story and the two
facts each derives: its mode and its wire. `StoryMode` is the mode a row
was written in, as the `mode` column records it.
"""

import json
from dataclasses import replace

from otaku.store.schema import (
    Attachment,
    CompletionType,
    InjectionPosition,
    Layout,
    Message,
    StoryMode,
    StorySettingDB,
)

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


class TestLayout:
    """`Layout` — the four ways a story is read, and the two facts derived
    from one: its `mode` (the look) and its `completion_type` (the wire,
    given whether the model in use can complete text)."""

    def test_each_name_reads_back_as_itself(self) -> None:
        assert [each.value for each in Layout] == ["novel", "canvas", "manuscript", "chat"]
        for layout in Layout:
            assert Layout.from_value(layout.value) is layout

    def test_anything_else_is_none(self) -> None:
        for said in ("Canvas", "", "story", 1, None, True, ["canvas"]):
            assert Layout.from_value(said) is None, said

    def test_the_canvas_and_the_novel_are_story_mode_and_the_rest_chat(self) -> None:
        assert Layout.NOVEL.mode is StoryMode.STORY
        assert Layout.CANVAS.mode is StoryMode.STORY
        assert Layout.MANUSCRIPT.mode is StoryMode.CHAT
        assert Layout.CHAT.mode is StoryMode.CHAT

    def test_the_text_wire_is_the_novels_alone_and_only_where_the_model_can(self) -> None:
        assert Layout.NOVEL.completion_type(True) is CompletionType.TEXT
        # a novel reopened on a model that cannot complete text plays as a canvas
        assert Layout.NOVEL.completion_type(False) is CompletionType.CHAT
        for other in (Layout.CANVAS, Layout.MANUSCRIPT, Layout.CHAT):
            assert other.completion_type(True) is CompletionType.CHAT, other
            assert other.completion_type(False) is CompletionType.CHAT, other
        assert {each.value for each in CompletionType} == {"chat", "text"}


class TestStoryMode:
    """`StoryMode` — what the `mode` column holds: the stored word reads
    back, anything else as CHAT, which is also a row's mode when nothing
    says otherwise (an imported row)."""

    def test_each_word_reads_back_as_itself(self) -> None:
        assert {each.value for each in StoryMode} == {"story", "chat"}
        for mode in StoryMode:
            assert StoryMode.from_value(mode.value) is mode

    def test_anything_else_reads_as_chat(self) -> None:
        for said in ("block", "inline", "none", "Story", "", None, 1):
            assert StoryMode.from_value(said) is StoryMode.CHAT, said

    def test_a_row_is_chat_unless_it_says_otherwise(self) -> None:
        assert Message(role="user", body="Hi").mode is StoryMode.CHAT
        written = replace(Message(role="user", body="Hi"), mode=StoryMode.STORY)
        assert written.mode is StoryMode.STORY


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

    def test_a_layout_is_read_by_its_name_and_anything_else_is_unsaid(self) -> None:
        raw = json.dumps({"layout": {"enabled": True, "layout": "novel"}})
        assert StorySettingDB.from_json(raw) == (
            StorySettingDB("layout", enabled=True, layout=Layout.NOVEL),
        )
        for said in ("Canvas", "story", 3, None):
            raw = json.dumps({"layout": {"layout": said}})
            assert StorySettingDB.from_json(raw) == (StorySettingDB("layout"),), said

    def test_a_written_layout_reads_back(self) -> None:
        row = StorySettingDB("layout", enabled=True, layout=Layout.CHAT)
        assert StorySettingDB.from_json(StorySettingDB.to_json([row], "")) == (row,)
        assert json.loads(StorySettingDB.to_json([row], "")) == {
            "layout": {"enabled": True, "layout": "chat"}
        }

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
