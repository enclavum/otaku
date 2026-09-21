"""A story's features: what each switch stands at, and what comes of it.

`ALL_FEATURES` names every feature — the global reminder, the story's, then
each registered tool. `read` answers them all, in that order, as the
settings JSON stands — the default where it says
nothing, garbles it, or names a position outside the feature's
`allowed_positions`: "system" and -1 … -8 for a tool, the numbers
alone for a reminder. `write` puts one feature's state into that JSON and keeps
every key it does not know. `injections` is what the switched-on
features put in the context, in the order given: a tool's instruction
as the prompts hold it, enclosed out of character in chat and bare in
the system message; a reminder as the user wrote it; an empty one
nothing.
"""

import json
from dataclasses import fields, replace

from otaku.backend.features import (
    ALL_FEATURES,
    USE_GLOBAL_REMINDER,
    USE_STORY_REMINDER,
    Feature,
    injections,
    read,
    write,
)
from otaku.context.tools import TOOLS
from otaku.settings.prompts import Prompts

PROMPTS = replace(Prompts(), ask_instruction="ASK HOW", notes_instruction="NOTE HOW")
ASK = "allow_questions"


class TestFeatures:
    def test_the_reminders_lead_and_every_registered_tool_follows(self) -> None:
        tools = [tool.feature for tool in TOOLS.values()]
        assert list(ALL_FEATURES) == [USE_GLOBAL_REMINDER, USE_STORY_REMINDER, *tools]

    def test_the_names_are_what_stories_are_stored_under(self) -> None:
        # A story keeps its switches under these names: a rename here
        # silently switches every stored story off.
        assert set(ALL_FEATURES) == {
            "allow_questions",
            "allow_assistant_notes",
            "use_story_reminder",
            "use_global_reminder",
        }


class TestRead:
    def test_a_story_without_settings_has_every_feature_off(self) -> None:
        found = read("")
        assert tuple(f.name for f in found) == ALL_FEATURES
        assert not any(f.on for f in found)

    def test_the_defaults_stand_where_the_settings_say_nothing(self) -> None:
        ask, story = feature(read(""), ASK), feature(read(""), USE_STORY_REMINDER)
        assert ask.position == -1
        assert (story.position, story.text) == (-3, "")

    def test_what_the_settings_say_is_read(self) -> None:
        raw = json.dumps(
            {
                ASK: {"on": True, "position": "system"},
                "use_story_reminder": {"on": True, "position": -5, "text": "Stay grim."},
                "use_global_reminder": {"on": True, "position": -2},
            }
        )
        found = read(raw)
        ask, story, shared = (
            feature(found, ASK),
            feature(found, USE_STORY_REMINDER),
            feature(found, USE_GLOBAL_REMINDER),
        )
        assert (ask.on, ask.position) == (True, "system")
        assert (story.on, story.position, story.text) == (True, -5, "Stay grim.")
        assert (shared.on, shared.position) == (True, -2)

    def test_the_allowed_positions_are_a_closed_list(self) -> None:
        from_the_end = (-1, -2, -3, -4, -5, -6, -7, -8)
        assert feature(read(""), ASK).allowed_positions == ("system", *from_the_end)
        # A reminder has no "system": it exists because a model forgets it.
        for name in (USE_GLOBAL_REMINDER, USE_STORY_REMINDER):
            assert feature(read(""), name).allowed_positions == from_the_end

    def test_a_position_outside_the_list_reads_as_the_default(self) -> None:
        raw = json.dumps({"use_story_reminder": {"on": True, "position": "system"}})
        assert feature(read(raw), USE_STORY_REMINDER).position == -3
        for said in (-9, 0, 2, "end", "-2", True, -1.0, None):
            raw = json.dumps({ASK: {"position": said}})
            assert feature(read(raw), ASK).position == -1, said

    def test_only_the_story_reminder_has_a_text(self) -> None:
        raw = json.dumps({ASK: {"text": "x"}, "use_global_reminder": {"text": "y"}})
        assert not any(f.text for f in read(raw))

    def test_a_switch_is_on_only_when_it_says_true(self) -> None:
        for said in ("yes", 1, None):
            assert not feature(read(json.dumps({ASK: {"on": said}})), ASK).on

    def test_settings_that_are_not_an_object_read_as_none(self) -> None:
        for raw in ("not json", "[1, 2]", '"text"', '{ASK: "on"}'):
            assert read(raw) == read("")


class TestWrite:
    def test_a_written_state_reads_back(self) -> None:
        state = replace(
            feature(read(""), USE_STORY_REMINDER), on=True, position=-4, text="Stay grim."
        )
        assert feature(read(write("", state)), USE_STORY_REMINDER) == state

    def test_every_key_it_does_not_know_is_kept(self) -> None:
        # A JSON key bumps no schema version: an older build can meet a
        # newer one's settings, and must hand them back whole.
        raw = json.dumps({"story_mode": {"on": True}, ASK: {"on": False, "tone": "dry"}})
        written = json.loads(write(raw, replace(feature(read(raw), ASK), on=True)))
        assert written["story_mode"] == {"on": True}
        assert written[ASK]["tone"] == "dry"
        assert written[ASK]["on"] is True

    def test_each_feature_keeps_only_what_is_its_own(self) -> None:
        written = json.loads(raw_of(read("")))
        assert set(written[ASK]) == {"on", "position"}
        assert set(written["use_global_reminder"]) == {"on", "position"}  # its text is no story's
        assert set(written["use_story_reminder"]) == {"on", "position", "text"}

    def test_over_garbage_it_starts_clean(self) -> None:
        written = json.loads(write("not json", replace(feature(read(""), ASK), on=True)))
        assert written == {ASK: {"on": True, "position": -1}}


class TestInjections:
    def test_nothing_switched_on_injects_nothing(self) -> None:
        assert injections(read(""), PROMPTS, "A global reminder.") == ()

    def test_a_tool_in_chat_is_enclosed_out_of_character(self) -> None:
        (found,) = injections(on(ASK), PROMPTS, "")
        assert (found.name, found.text, found.position) == (ASK, "((OOC: ASK HOW))", -1)

    def test_a_tool_in_the_system_message_is_bare(self) -> None:
        (found,) = injections(on(ASK, position="system"), PROMPTS, "")
        assert (found.text, found.position) == ("ASK HOW", "system")

    def test_the_story_reminder_goes_as_the_user_wrote_it(self) -> None:
        (found,) = injections(
            on(USE_STORY_REMINDER, text="[Style: terse]", position=-4), PROMPTS, ""
        )
        assert (found.text, found.position) == ("[Style: terse]", -4)

    def test_the_global_reminder_sends_the_text_every_story_shares(self) -> None:
        (found,) = injections(on(USE_GLOBAL_REMINDER), PROMPTS, "Stay grim.\n")
        assert (found.name, found.text, found.position) == (USE_GLOBAL_REMINDER, "Stay grim.", -3)

    def test_each_reminder_is_a_switch_of_its_own(self) -> None:
        story_only = on(USE_STORY_REMINDER, text="In this one, rain.")
        assert [i.text for i in injections(story_only, PROMPTS, "Stay grim.")] == [
            "In this one, rain."
        ]

    def test_a_reminder_with_nothing_to_say_is_not_sent(self) -> None:
        assert injections(on(USE_STORY_REMINDER), PROMPTS, "") == ()
        assert injections(on(USE_GLOBAL_REMINDER), PROMPTS, "  ") == ()

    def test_the_global_reminder_leads_the_story_s_and_the_tools_follow(self) -> None:
        states = on(USE_STORY_REMINDER, text="In this one, rain.")
        for name in ALL_FEATURES:
            states = read(write(raw_of(states), replace(feature(states, name), on=True)))
        names = [i.name for i in injections(states, PROMPTS, "Stay grim.")]
        assert names == list(ALL_FEATURES)


class TestRegistry:
    def test_every_tool_names_an_instruction_the_prompts_hold(self) -> None:
        known = {f.name for f in fields(Prompts)}
        for tool in TOOLS.values():
            assert tool.instruction_field in known, tool.__name__

    def test_every_tool_has_a_label(self) -> None:
        for tool in TOOLS.values():
            assert tool.label, tool.__name__


def feature(found: tuple[Feature, ...], name: str) -> Feature:
    return next(f for f in found if f.name == name)


def raw_of(found: tuple[Feature, ...]) -> str:
    raw = ""
    for state in found:
        raw = write(raw, state)
    return raw


def on(name: str, **changes: object) -> tuple[Feature, ...]:
    """The features with `name` switched on, `changes` applied to it."""
    found = read("")
    state = replace(feature(found, name), on=True, **changes)  # type: ignore[arg-type]
    return read(write("", state))
