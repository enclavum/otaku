"""A story's features on the wire and in the store: a tool switched on
sends its instruction from prompts.toml where the story says — enclosed
above the newest line, or bare after the premise; a reminder sends the
user's own words from the end — the global one's, written once for
every story, and the story's own, each a switch; none of it is ever
stored as a message; the switches are the story's — kept across a
launch, carried by a fork, off in a new story."""

import pytest

from otaku.backend.api import features as api_features
from otaku.backend.paths import Paths
from otaku.backend.session import Refused
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch

HOW_TO_ASK = "ASK LIKE THIS"


class TestTools:
    def test_a_tool_switched_on_sends_its_instruction_above_the_newest_line(
        self, server, tmp_path
    ) -> None:
        # The instruction comes FROM prompts.toml, so the file is edited:
        # asserting the built-in wording would pass with the load path gone.
        app = with_instruction(tmp_path / "state", server)
        try:
            api_features.update(app.session, "allow_questions", on=True)
            app.play("I push the door.")
            assert messages(app, "I push the door.") == [
                ("user", f"((OOC: {HOW_TO_ASK}))\n\nI push the door.")
            ]
            # Sent, never stored: the story holds the line as typed.
            (line, _reply) = app.store.stories.get_messages(app.session.story_id)
            assert line.body == "I push the door."
        finally:
            app.close()

    def test_switched_off_it_is_gone(self, server, tmp_path) -> None:
        app = with_instruction(tmp_path / "state", server)
        try:
            api_features.update(app.session, "allow_questions", on=True)
            app.play("I push the door.")
            api_features.update(app.session, "allow_questions", on=False)
            app.play("I step inside.")
            sent = messages(app, "I step inside.")
            assert not any(HOW_TO_ASK in body for _, body in sent)
        finally:
            app.close()

    def test_in_the_system_message_it_follows_the_premise_bare(self, server, tmp_path) -> None:
        app = with_instruction(tmp_path / "state", server)
        try:
            app.play("/system Be terse.")
            api_features.update(app.session, "allow_questions", on=True, position="system")
            app.play("I push the door.")
            assert messages(app, "I push the door.") == [
                ("system", f"Be terse.\n\n{HOW_TO_ASK}"),
                ("user", "I push the door."),
            ]
            # The premise itself is the user's text, untouched.
            assert app.store.stories.get_system(app.session.story_id) == "Be terse."
        finally:
            app.close()

    def test_a_position_counts_messages_from_the_end(self, server, tmp_path) -> None:
        app = with_instruction(tmp_path / "state", server)
        try:
            app.play("One.")
            app.play("Two.")
            api_features.update(app.session, "allow_questions", on=True, position=-3)
            app.play("Three.")
            bodies = [body for _, body in messages(app, "Three.")]
            assert bodies[2] == f"((OOC: {HOW_TO_ASK}))\n\nTwo."
            assert bodies[4] == "Three."
        finally:
            app.close()


class TestReminders:
    def test_both_go_as_written_the_global_one_leading(self, app: App) -> None:
        api_features.set_global_reminder(app.session, "Stay grim.")
        api_features.update(app.session, "use_global_reminder", on=True, position=-1)
        api_features.update(
            app.session, "use_story_reminder", on=True, position=-1, text="[Here: rain]"
        )
        app.play("I push the door.")
        assert messages(app, "I push the door.") == [
            ("user", "Stay grim.\n\n[Here: rain]\n\nI push the door.")
        ]

    def test_each_is_a_switch_of_its_own(self, app: App) -> None:
        api_features.set_global_reminder(app.session, "Stay grim.")
        api_features.update(
            app.session, "use_story_reminder", on=True, position=-1, text="[Here: rain]"
        )
        app.play("I push the door.")
        assert messages(app, "I push the door.") == [("user", "[Here: rain]\n\nI push the door.")]

    def test_each_rides_at_its_own_position(self, app: App) -> None:
        api_features.set_global_reminder(app.session, "Stay grim.")
        app.play("One.")
        api_features.update(app.session, "use_global_reminder", on=True, position=-3)
        api_features.update(
            app.session, "use_story_reminder", on=True, position=-1, text="[Here: rain]"
        )
        app.play("Two.")
        bodies = [body for _, body in messages(app, "Two.")]
        assert bodies[0] == "Stay grim.\n\nOne."
        assert bodies[2] == "[Here: rain]\n\nTwo."

    def test_the_global_text_is_every_story_s_and_the_switch_each_story_s(self, app: App) -> None:
        api_features.set_global_reminder(app.session, "Stay grim.")
        api_features.update(app.session, "use_global_reminder", on=True, position=-1)
        app.play("I push the door.")
        app.play("/new")
        app.play("A second story.")  # a new story has it off
        assert messages(app, "A second story.") == [("user", "A second story.")]
        api_features.update(app.session, "use_global_reminder", on=True, position=-1)
        app.play("And on.")
        assert messages(app, "And on.")[-1] == ("user", "Stay grim.\n\nAnd on.")

    def test_cleared_it_is_gone_from_the_store(self, app: App) -> None:
        api_features.set_global_reminder(app.session, "Stay grim.")
        assert app.store.globals.get("reminder") == "Stay grim."
        assert api_features.get_global_reminder(app.session) == "Stay grim."
        api_features.set_global_reminder(app.session, "")
        assert app.store.globals.get("reminder") == ""
        assert api_features.get_global_reminder(app.session) == ""


class TestTheStorysOwn:
    def test_a_switch_flipped_before_the_first_turn_is_kept(self, server, tmp_path) -> None:
        app = with_instruction(tmp_path / "state", server)
        try:
            assert app.session.story_id is None
            api_features.update(app.session, "allow_questions", on=True)
            app.play("I push the door.")
            assert HOW_TO_ASK in messages(app, "I push the door.")[-1][1]
            assert '"allow_questions"' in app.store.stories.get_settings(app.session.story_id)
        finally:
            app.close()

    def test_the_switches_survive_a_launch(self, server, tmp_path) -> None:
        app = with_instruction(tmp_path / "state", server)
        api_features.update(app.session, "allow_questions", on=True, position=-2)
        app.play("I push the door.")
        app.close()
        app = launch(tmp_path / "state", server)
        try:
            ask = next(f for f in api_features.get(app.session) if f.name == "allow_questions")
            assert (ask.on, ask.position) == (True, -2)
        finally:
            app.close()

    def test_a_new_story_starts_with_every_feature_off(self, app: App) -> None:
        api_features.update(app.session, "allow_questions", on=True)
        app.play("I push the door.")
        app.play("/new")
        assert not any(feature.on for feature in api_features.get(app.session))

    def test_a_fork_plays_the_way_its_origin_does(self, app: App) -> None:
        api_features.update(app.session, "allow_assistant_notes", on=True, position="system")
        app.play("I push the door.")
        origin = app.session.story_id
        app.play("/fork")
        assert app.session.story_id != origin
        assert app.store.stories.get_settings(app.session.story_id) == (
            app.store.stories.get_settings(origin)
        )
        notes = next(f for f in api_features.get(app.session) if f.name == "allow_assistant_notes")
        assert (notes.on, notes.position) == (True, "system")


class TestRefusals:
    def test_what_a_feature_cannot_take_is_refused(self, app: App) -> None:
        for asked in (
            {"name": "plan", "on": True},
            {"name": "allow_questions", "position": "end"},
            {"name": "allow_questions", "position": 0},
            {"name": "allow_questions", "position": -9},
            {"name": "allow_questions", "position": "-2"},
            {"name": "allow_questions", "text": "Ask about the weather."},
            # A reminder is never the system message's: that is the premise's.
            {"name": "use_story_reminder", "position": "system"},
            {"name": "use_global_reminder", "position": "system"},
            # The global reminder's text is every story's, not one story's.
            {"name": "use_global_reminder", "text": "Stay grim."},
        ):
            with pytest.raises(Refused):
                api_features.update(app.session, **asked)  # type: ignore[arg-type]
        # Nothing was written by a refusal.
        assert not any(feature.on for feature in api_features.get(app.session))


def with_instruction(root, server) -> App:
    """The app over a prompts.toml whose ask instruction is `HOW_TO_ASK`."""
    paths = Paths.resolve(root)
    paths.ensure_tree()
    paths.prompts_file.write_text(f'ask_instruction = "{HOW_TO_ASK}"\n')
    return launch(root, server)


def messages(app: App, last_line: str) -> list[tuple[str, str]]:
    """The turn's request as (role, text) pairs — picked by its newest
    line, since a background request may have landed after it."""
    request = scripted.chat_request(app.server, last_line)
    return [(m["role"], scripted.content_text(m)) for m in request["messages"]]
