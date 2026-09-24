"""Managing stories: browsing and resuming, forking, /title, /system, /new, and a
story's settings.

The browser itself is a full-screen surface driven headless below; for
the routing stories it is a stubbed `screens.stories.pick` — patched at
its module seam — that EXECUTES the settled selection through
`api.stories.land`, exactly as the real screen does (the screens' one
ownership rule). These stories are about what a settled selection
MEANS — resume, fork, truncate — and about the story commands' effects
on the store, the session, and the remembered state.

A story's settings on the wire and in the store: a tool switched on
sends its instruction from prompts.toml where the story says — enclosed
above the newest line, or bare after the premise; a reminder sends the
user's own words from the end — the shared one's, written once, and the
story's own, each a switch; none of it is ever stored as a message; the
switches are the story's — kept across a launch, carried by a fork, off
in a new story.
"""

import contextlib
from collections.abc import Callable

import pytest

from otaku.backend import InjectionPosition
from otaku.backend.api import settings as api_settings
from otaku.backend.api import stories as api_stories
from otaku.backend.paths import Paths
from otaku.backend.session import Refused, Session
from otaku.backend.story import StorySetting
from otaku.settings.prompts import Prompts
from otaku.terminal.screens import stories as screen_stories
from otaku.terminal.screens import story as screen_story
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch
from scenarios.support.screens import (
    BACKSPACE,
    CTRL_S,
    DELETE,
    DOWN,
    ENTER,
    ESC,
    LEFT,
    RIGHT,
    SPACE,
    TAB,
    run_screen,
)

Picker = Callable[[Session], "str | None"]
# The ask instruction the settings stories put into prompts.toml.
HOW_TO_ASK = "ASK LIKE THIS"


class TestBrowsing:
    def test_picking_a_story_resumes_it(self, app: App, capsys, monkeypatch) -> None:
        first, _second = two_stories(app)
        app.play("/system The second premise.")
        monkeypatch.setattr(screen_stories, "pick", picks(first))
        capsys.readouterr()
        app.play("/stories")

        out = capsys.readouterr().out
        assert "Resumed at message 2." in out
        assert "The first story begins." in out  # the scene is echoed back
        assert app.session.story_id == first
        assert app.session.system == ""  # the second story's premise stayed behind
        assert [m.body for m in app.session.messages] == [
            "The first story begins.",
            scripted.CHAT_REPLY,
        ]
        # The resume is remembered: a bare relaunch lands in that story.
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.story_id == first
        relaunched.close()

    def test_an_edit_in_a_cancelled_browse_reaches_the_session(self, app: App, monkeypatch) -> None:
        app.play("I enter the hall.")
        first_id = app.session.messages[0].id

        # The browser edits a message in place, then closes with no pick —
        # the edit goes through the api, which keeps the session in step.
        def edit_and_cancel(session: Session) -> None:
            api_stories.edit_message(session, first_id, "I enter the throne hall.")
            return None

        monkeypatch.setattr(screen_stories, "pick", edit_and_cancel)
        app.play("/stories")
        assert app.session.messages[0].body == "I enter the throne hall."


class TestResumeDialog:
    """Picking an EARLIER message: the browser's dialog settled the action —
    the command executes a fork or a truncation (cancel never leaves the
    browser)."""

    def decided(self, app: App, monkeypatch, story_id: int, action: str) -> None:
        monkeypatch.setattr(screen_stories, "pick", picks(story_id, upto=1, action=action))
        app.play("/stories")

    def test_fork_copies_at_the_picked_message(self, app: App, capsys, monkeypatch) -> None:
        first, second = two_stories(app)
        old_head = app.store.stories.get_head(first)
        self.decided(app, monkeypatch, first, "fork")

        out = capsys.readouterr().out
        assert "Forked to: First - 2. Continued from message 1." in out
        fork = app.session.story_id
        assert fork not in (first, second)
        assert app.store.stories.get(fork).forked_from_id == first  # lineage, for the record
        assert [m.body for m in app.session.messages] == ["The first story begins."]
        # The copy is deep: its message is its own row, not a shared one.
        assert app.session.messages[0].id != app.store.stories.get_messages(first)[0].id
        # The original did not move.
        assert app.store.stories.get_head(first) == old_head

    def test_truncate_rewinds_the_head_keeping_siblings(
        self, app: App, capsys, monkeypatch
    ) -> None:
        first, _ = two_stories(app)
        reply_id = app.store.stories.get_messages(first)[1].id
        self.decided(app, monkeypatch, first, "truncate")

        assert "Truncated at message 1." in capsys.readouterr().out
        assert app.session.story_id == first
        assert [m.body for m in app.session.messages] == ["The first story begins."]
        # The abandoned reply still exists in the tree — nothing was deleted.
        assert app.store.messages.count_body_chars([reply_id]) > 0


class TestStoryBrowser:
    def pick(self, app: App, keys: str):
        return run_screen(keys, lambda: screen_stories.pick(app.session))

    def test_enter_on_a_story_with_nothing_played_resumes_it(self, app: App) -> None:
        # Nothing played means nothing to pick, and the dossier's messages
        # tab would open on nothing with no way to land: Enter on the row
        # resumes the story itself, so a story started and left is not
        # one that can only be deleted.
        app.play("I enter the hall.")
        app.play("/title Played")
        played = app.session.story_id
        app.play("/new Blank")
        blank = app.session.story_id
        assert self.pick(app, "/Played" + ENTER + ENTER) is not None
        assert app.session.story_id == played
        assert self.pick(app, "/Blank" + ENTER) is not None
        assert app.session.story_id == blank
        assert app.session.messages == []
        app.play("A different beginning.")
        assert app.session.story_id == blank  # the turn lands in it
        assert len(app.store.stories.get_messages(blank)) == 2

    def test_e_edits_a_message_in_place(self, app: App) -> None:
        _first, second = two_stories(app)
        assert self.pick(app, ENTER + "e" + "!" + CTRL_S + ESC + ESC) is None
        chain = app.store.stories.get_messages(second)
        assert chain[-1].body == "!" + scripted.CHAT_REPLY

    def test_the_dossier_edits_another_story_s_premise(self, app: App) -> None:
        # Any story opens whole and any story EDITS: drilled into the
        # row under the cursor — not the open one — the premise tab's
        # save lands on that story, and the open story keeps its own.
        first, _second = two_stories(app)
        assert self.pick(app, DOWN + ENTER + LEFT + ENTER + "!" + CTRL_S + ESC + ESC) is None
        assert app.store.stories.get_system(first) == "!"
        assert app.session.system == ""  # the open story's premise is untouched

    def test_the_dossier_clears_a_premise(self, app: App) -> None:
        # An emptied editor is a save too: the premise is removed, not
        # reported — the trap this covers is "" falling through to the
        # report and the clear silently not landing.
        first, _second = two_stories(app)
        assert self.pick(app, DOWN + ENTER + LEFT + ENTER + "!" + CTRL_S + ESC + ESC) is None
        assert app.store.stories.get_system(first) == "!"
        keys = DOWN + ENTER + LEFT + ENTER + DELETE + CTRL_S + ESC + ESC
        assert self.pick(app, keys) is None
        assert app.store.stories.get_system(first) == ""

    def test_the_dossier_edits_another_story_s_message(self, app: App) -> None:
        first, _second = two_stories(app)
        assert self.pick(app, DOWN + ENTER + "e" + "!" + CTRL_S + ESC + ESC) is None
        assert app.store.stories.get_messages(first)[-1].body == "!" + scripted.CHAT_REPLY

    def test_the_dossier_edits_another_story_s_lore(self, app: App) -> None:
        # The scenes tab of the story under the cursor — the write is
        # addressed to THAT story and passes its ownership check.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        first = app.session.story_id
        app.play("/new")
        app.play("The second story begins.")
        keys = DOWN + ENTER + RIGHT + ENTER + DOWN + ENTER + "!" + CTRL_S + ESC * 3
        assert self.pick(app, keys) is None
        ids = app.store.stories.get_messages_ids(first)
        scene = app.store.scenes.get_current(first, ids)[0]
        assert scene.summary == "!A guest came in and met the Keeper."

    def test_delete_removes_a_story_after_a_confirm(self, app: App) -> None:
        first, _second = two_stories(app)
        assert self.pick(app, DELETE + "y" + ESC) is None
        remaining = [row.id for row in app.store.stories.list()]
        assert remaining == [first]  # the newest row was deleted

    def test_the_mac_delete_key_deletes_too(self, app: App) -> None:
        # macOS captions its backspace key "delete" — with no filter open
        # it must mean what it says.
        first, _second = two_stories(app)
        assert self.pick(app, BACKSPACE + "y" + ESC) is None
        assert [row.id for row in app.store.stories.list()] == [first]

    def test_backspace_inside_the_filter_never_deletes(self, app: App) -> None:
        # While a filter is open backspace edits it, so the `y` lands in
        # the query — no confirm ever came up, and no story goes anywhere.
        first, second = two_stories(app)
        assert self.pick(app, "/x" + BACKSPACE + "y" + ESC + ESC) is None
        assert [row.id for row in app.store.stories.list()] == [second, first]


class TestFork:
    def test_fork_switches_to_the_copy_and_leaves_the_original(self, app: App) -> None:
        app.play("I enter the hall.")
        original = app.session.story_id
        original_ids = [m.id for m in app.session.messages]
        app.play("/fork")

        fork = app.session.story_id
        assert fork != original
        assert [m.body for m in app.session.messages] == ["I enter the hall.", scripted.CHAT_REPLY]
        assert [m.id for m in app.session.messages] != original_ids  # fresh rows
        assert [m.body for m in app.store.stories.get_messages(original)] == [
            "I enter the hall.",
            scripted.CHAT_REPLY,
        ]
        # The fork is what a relaunch resumes now.
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.story_id == fork
        relaunched.close()

    def test_an_untitled_story_forks_untitled(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/fork")
        # No title to inherit: the copy is named by its opening line, the
        # way every untitled story is named.
        assert "Forked to: I enter the hall." in capsys.readouterr().out
        assert app.store.stories.get(app.session.story_id).title == ""

    def test_forks_of_a_titled_story_number_themselves(self, app: App, capsys, monkeypatch) -> None:
        app.play("I enter the hall.")
        app.play("/title Hall")
        origin = app.session.story_id
        app.play("/fork")
        assert app.store.stories.get(app.session.story_id).title == "Hall - 2"
        monkeypatch.setattr(screen_stories, "pick", picks(origin))
        app.play("/stories")  # back to the origin, then fork again
        app.play("/fork")
        assert app.store.stories.get(app.session.story_id).title == "Hall - 3"

    def test_an_explicit_title_is_used_verbatim(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/fork Another door")
        assert app.store.stories.get(app.session.story_id).title == "Another door"

    def test_the_memory_travels_with_the_copy(self, app: App) -> None:
        # A branch is the story so far, memory included — whatever the
        # settle margin is. A 3-turn story is shorter than the default 20,
        # which used to leave every scene behind and the fork blank.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/fork")
        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        scenes = app.store.scenes.get_current(story_id, ids)
        assert len(scenes) == 1
        assert scenes[0].history == "A guest came in and met the Keeper."
        cast = app.store.characters.list(story_id)
        assert [c.name for c in cast] == ["Keeper"]
        assert app.store.journals.get_current(story_id, ids)[cast[0].id].state == "at the gate"


class TestSystem:
    def test_system_before_the_first_turn_lands_on_the_created_story(self, app: App) -> None:
        app.play("/system You are the narrator.")
        assert app.session.story_id is None  # /system alone creates nothing
        app.play("I enter the hall.")
        assert app.store.stories.get_system(app.session.story_id) == "You are the narrator."

    def test_system_on_a_live_story_persists(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/system Answer briefly.")
        assert "System prompt set (15 chars)." in capsys.readouterr().out
        assert app.store.stories.get_system(app.session.story_id) == "Answer briefly."

    def test_a_bare_system_reports_and_changes_nothing(self, app: App, capsys) -> None:
        app.play("/system Answer briefly.")
        capsys.readouterr()
        app.play("/system")
        assert "Answer briefly." in capsys.readouterr().out  # the premise, reported
        assert app.session.system == "Answer briefly."

    def test_a_dash_clears_the_premise(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/system Answer briefly.")
        app.play("/system -")
        assert app.session.system == ""
        assert app.store.stories.get_system(app.session.story_id) == ""
        # The clear reaches the wire: the next request opens with the
        # played story, no system row.
        app.play("I look around.")
        assert app.server.requests[-1]["messages"][0]["role"] == "user"

    def test_a_long_premise_is_text_not_a_filename(self, app: App, capsys) -> None:
        # An argument is TEXT until proven a path, and the proof is a
        # question the filesystem can refuse: over 255 bytes in one
        # component it raises ENAMETOOLONG rather than answering, which
        # used to crash the command on any premise worth writing.
        premise = "You are the narrator of a careful story. " * 12  # ~480 chars, no slashes
        app.play(f"/system {premise}")
        app.play("I enter the hall.")
        assert app.store.stories.get_system(app.session.story_id) == premise.strip()
        assert "Traceback" not in capsys.readouterr().out

    def test_a_multiline_premise_survives_whole(self, app: App) -> None:
        # What a `/system """…"""` block collects: newlines and all, and
        # no stray delimiters in the stored text.
        premise = "## Premise\n\nA quiet story.\n- one rule\n- another"
        app.play(f"/system {premise}")
        app.play("I enter the hall.")
        assert app.store.stories.get_system(app.session.story_id) == premise

    def test_a_file_argument_supplies_the_prompt(self, app: App, tmp_path) -> None:
        premise = tmp_path / "premise.md"
        premise.write_text("You are the narrator.\n", encoding="utf-8")
        app.play(f"/system {premise}")
        app.play("I enter the hall.")
        assert app.store.stories.get_system(app.session.story_id) == "You are the narrator."

    def test_the_completion_trigger_is_not_part_of_the_name(self, app: App, tmp_path) -> None:
        premise = tmp_path / "premise.md"
        premise.write_text("You are the narrator.", encoding="utf-8")
        app.play(f"/system @{premise}")
        assert app.session.system == "You are the narrator."

    def test_text_naming_no_file_stays_literal(self, app: App) -> None:
        app.play("/system /nowhere/gone.md")
        assert app.session.system == "/nowhere/gone.md"

    def test_an_unreadable_file_changes_nothing(self, app: App, capsys, tmp_path) -> None:
        sealed = tmp_path / "sealed.md"
        sealed.write_text("You are the narrator.", encoding="utf-8")
        sealed.chmod(0o000)
        app.play("/system The premise stands.")
        app.play(f"/system {sealed}")
        assert "Could not read" in capsys.readouterr().out
        assert app.session.system == "The premise stands."

    def test_an_empty_file_changes_nothing(self, app: App, capsys, tmp_path) -> None:
        empty = tmp_path / "empty.md"
        empty.write_text("", encoding="utf-8")
        app.play("/system The premise stands.")
        app.play(f"/system {empty}")
        assert "empty — system prompt unchanged" in capsys.readouterr().out
        assert app.session.system == "The premise stands."


class TestTitle:
    def test_title_titles_the_story(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/title The Throne Hall")
        assert 'Story title set to "The Throne Hall".' in capsys.readouterr().out
        assert app.store.stories.get(app.session.story_id).title == "The Throne Hall"

    def test_title_before_the_first_turn_creates_the_story(self, app: App) -> None:
        assert app.session.story_id is None
        app.play("/title The Planned Story")
        story_id = app.session.story_id
        assert story_id is not None
        assert app.store.stories.get(story_id).title == "The Planned Story"
        app.play("I enter the hall.")  # the first turn lands in that same story
        assert app.session.story_id == story_id

    def test_title_does_not_reorder_the_story_list(self, app: App, monkeypatch) -> None:
        first, second = two_stories(app)
        monkeypatch.setattr(screen_stories, "pick", picks(first))
        app.play("/stories")
        app.play("/title Renamed Later")
        # Titling is metadata: the list still leads with the recently
        # PLAYED story, not the recently renamed one.
        assert app.store.stories.list()[0].id == second


class TestNew:
    def test_new_starts_the_story_at_once(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        original = app.session.story_id
        app.play("/new")
        assert "Started a new story." in capsys.readouterr().out
        started = app.session.story_id
        assert started is not None and started != original
        # Browsable before its first turn — the reason it is created here
        # and not at the turn.
        assert started in {row.id for row in app.store.stories.list()}
        assert app.session.messages == []

        app.play("A different beginning.")
        assert app.session.story_id == started  # the turn lands in it, not in another
        # The left story is intact, ready to be resumed from the browser.
        assert len(app.store.stories.get_messages(original)) == 2

    def test_the_started_story_is_where_a_relaunch_lands(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/new")
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.story_id == app.session.story_id
        relaunched.close()

    def test_new_names_the_story_it_starts(self, app: App, capsys) -> None:
        app.play("/new The Second Tale")
        assert "The Second Tale" in capsys.readouterr().out
        assert app.store.stories.get(app.session.story_id).title == "The Second Tale"
        # The name is on the row, so /title and the browser read it from
        # the one place a title lives.
        assert app.store.stories.list()[0].label == "The Second Tale"


class TestSettingsOfTools:
    def test_a_tool_switched_on_sends_its_prompt_before_the_newest_line(
        self, server, tmp_path
    ) -> None:
        # The prompt comes FROM prompts.toml, so the file is edited:
        # asserting the built-in wording would pass with the load path gone.
        app = with_prompt(tmp_path / "state", server)
        try:
            api_stories.update_setting(app.session, "allow_questions", enabled=True)
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
        app = with_prompt(tmp_path / "state", server)
        try:
            api_stories.update_setting(app.session, "allow_questions", enabled=True)
            app.play("I push the door.")
            api_stories.update_setting(app.session, "allow_questions", enabled=False)
            app.play("I step inside.")
            sent = messages(app, "I step inside.")
            assert not any(HOW_TO_ASK in body for _, body in sent)
        finally:
            app.close()

    def test_in_the_system_message_it_follows_the_premise_bare(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        try:
            app.play("/system Be terse.")
            api_stories.update_setting(
                app.session, "allow_questions", enabled=True, position=InjectionPosition()
            )
            app.play("I push the door.")
            assert messages(app, "I push the door.") == [
                ("system", f"Be terse.\n\n{HOW_TO_ASK}"),
                ("user", "I push the door."),
            ]
            # The premise itself is the user's text, untouched.
            assert app.store.stories.get_system(app.session.story_id) == "Be terse."
        finally:
            app.close()

    def test_a_position_counts_the_readers_messages_from_the_end(self, server, tmp_path) -> None:
        # 2 is before the reader's previous line: the replies between are
        # not counted.
        app = with_prompt(tmp_path / "state", server)
        try:
            app.play("One.")
            app.play("Two.")
            api_stories.update_setting(
                app.session, "allow_questions", enabled=True, position=InjectionPosition(2)
            )
            app.play("Three.")
            bodies = [body for _, body in messages(app, "Three.")]
            assert bodies[2] == f"((OOC: {HOW_TO_ASK}))\n\nTwo."
            assert bodies[4] == "Three."
        finally:
            app.close()


class TestSettingsOfReminders:
    def test_both_go_out_of_character_the_shared_one_leading(self, app: App) -> None:
        # A reminder rides a message of the reader's, where bare text
        # would read as the reader's line.
        api_stories.set_shared_reminder(app.session, "Stay grim.")
        api_stories.update_setting(
            app.session, "use_shared_reminder", enabled=True, position=InjectionPosition(1)
        )
        api_stories.update_setting(
            app.session,
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(1),
            reminder_text="[Here: rain]",
        )
        app.play("I push the door.")
        assert messages(app, "I push the door.") == [
            ("user", "((OOC: Stay grim.))\n\n((OOC: [Here: rain]))\n\nI push the door.")
        ]

    def test_each_is_a_switch_of_its_own(self, app: App) -> None:
        api_stories.set_shared_reminder(app.session, "Stay grim.")
        api_stories.update_setting(
            app.session,
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(1),
            reminder_text="[Here: rain]",
        )
        app.play("I push the door.")
        assert messages(app, "I push the door.") == [
            ("user", "((OOC: [Here: rain]))\n\nI push the door.")
        ]

    def test_each_rides_at_its_own_position(self, app: App) -> None:
        api_stories.set_shared_reminder(app.session, "Stay grim.")
        app.play("One.")
        api_stories.update_setting(
            app.session, "use_shared_reminder", enabled=True, position=InjectionPosition(2)
        )
        api_stories.update_setting(
            app.session,
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(1),
            reminder_text="[Here: rain]",
        )
        app.play("Two.")
        bodies = [body for _, body in messages(app, "Two.")]
        assert bodies[0] == "((OOC: Stay grim.))\n\nOne."
        assert bodies[2] == "((OOC: [Here: rain]))\n\nTwo."

    def test_the_text_is_shared_and_the_switch_is_each_story_s(self, app: App) -> None:
        api_stories.set_shared_reminder(app.session, "Stay grim.")
        api_stories.update_setting(
            app.session, "use_shared_reminder", enabled=True, position=InjectionPosition(1)
        )
        app.play("I push the door.")
        app.play("/new")
        app.play("A second story.")  # a new story has it off
        assert messages(app, "A second story.") == [("user", "A second story.")]
        api_stories.update_setting(
            app.session, "use_shared_reminder", enabled=True, position=InjectionPosition(1)
        )
        app.play("And on.")
        assert messages(app, "And on.")[-1] == ("user", "((OOC: Stay grim.))\n\nAnd on.")

    def test_a_reminder_with_nothing_written_sends_nothing(self, app: App) -> None:
        api_stories.update_setting(
            app.session, "use_story_reminder", enabled=True, position=InjectionPosition(1)
        )
        app.play("I push the door.")
        assert messages(app, "I push the door.") == [("user", "I push the door.")]

    def test_cleared_it_is_gone_from_the_store(self, app: App) -> None:
        api_stories.set_shared_reminder(app.session, "Stay grim.")
        assert app.store.settings.get("shared_reminder") == "Stay grim."
        assert api_stories.get_shared_reminder(app.session) == "Stay grim."
        api_stories.set_shared_reminder(app.session, "")
        assert app.store.settings.get("shared_reminder") == ""
        assert api_stories.get_shared_reminder(app.session) == ""


class TestToolsTab:
    """The dossier's Tools tab: one row per captioned setting — Space
    checks and unchecks it — with the setting's fields in the panel
    beside: Enter or Tab moves in, ↑/↓ walk the fields, Enter acts on
    one (a checkbox toggles, the depth opens its dropdown, a reminder's
    text edits in place, the instructions open the prompt over the
    panel), Esc returns to the list. Every change lands through
    `backend.api`, so the store is what the keys are held against."""

    def test_space_checks_and_unchecks_a_setting(self, app: App) -> None:
        app.play("I push the door.")
        self.tools(app, SPACE + ESC)
        assert self.setting(app, "allow_questions").enabled
        self.tools(app, SPACE + ESC)
        assert not self.setting(app, "allow_questions").enabled

    def test_the_depth_is_picked_from_the_dropdown(self, app: App) -> None:
        app.play("I push the door.")
        # questions: Enter into the panel lands on the instructions; ↓ to
        # the depth; Enter opens the closed list, ↓ picks the next place,
        # Enter sets it; Esc back to the list, Esc quits.
        self.tools(app, ENTER + DOWN + ENTER + DOWN + ENTER + ESC + ESC)
        assert self.setting(app, "allow_questions").injection_position == InjectionPosition(2)
        # Tab is the other way in, and never a way back
        self.tools(app, TAB + DOWN + ENTER + DOWN + ENTER + ESC + ESC)
        assert self.setting(app, "allow_questions").injection_position == InjectionPosition(3)

    def test_the_story_reminder_edits_in_place(self, app: App) -> None:
        app.play("I push the door.")
        # third row; its first field is the text: Enter edits, Ctrl+S saves
        self.tools(app, DOWN + DOWN + ENTER + ENTER + "Rain." + CTRL_S + ESC + ESC)
        assert self.setting(app, "use_story_reminder").reminder_text == "Rain."

    def test_the_shared_reminder_is_every_storys(self, app: App) -> None:
        app.play("I push the door.")
        self.tools(app, DOWN * 3 + ENTER + ENTER + "Stay grim." + CTRL_S + ESC + ESC)
        assert api_stories.get_shared_reminder(app.session) == "Stay grim."

    def test_the_notes_display_is_a_checkbox_in_the_panel(self, app: App) -> None:
        app.play("I push the door.")
        # second row: Space in the panel toggles the display; Space on the
        # list, after Esc, checks the tool itself
        self.tools(app, DOWN + ENTER + SPACE + ESC + SPACE + ESC)
        notes = self.setting(app, "allow_assistant_notes")
        assert (notes.enabled, notes.display_notes) == (True, False)

    def test_the_instructions_row_edits_the_tools_prompt(self, app: App) -> None:
        app.play("I push the door.")
        shipped = api_settings.get_tool_prompt(app.session, "question")
        # the row opens the prompt over the panel; typed at its start
        self.tools(app, ENTER + ENTER + "Be brief. " + CTRL_S + ESC + ESC)
        assert api_settings.get_tool_prompt(app.session, "question") == "Be brief. " + shipped
        # Esc in the editor writes nothing
        self.tools(app, ENTER + ENTER + "Dropped. " + ESC + ESC + ESC)
        assert api_settings.get_tool_prompt(app.session, "question") == "Be brief. " + shipped
        # the notes tool's instructions are its own
        self.tools(app, DOWN + ENTER + DOWN + ENTER + "Short. " + CTRL_S + ESC + ESC)
        assert api_settings.get_tool_prompt(app.session, "note").startswith("Short. ")

    def test_the_prompts_file_holds_what_was_saved(self, app: App) -> None:
        app.play("I push the door.")
        self.tools(app, ENTER + ENTER + "Be brief. " + CTRL_S + ESC + ESC)
        text = app.paths.prompts_file.read_text()
        assert "Be brief. " in text

    def tools(self, app: App, keys: str) -> None:
        # the dossier opens on the scenes; two tabs to the right sit the tools
        with contextlib.suppress(EOFError):
            run_screen(RIGHT + RIGHT + keys, lambda: screen_story.browse(app.session, "scenes"))

    def setting(self, app: App, name: str) -> StorySetting:
        found = api_stories.get_settings(app.session).get(name)
        assert found is not None
        return found


class TestSettingsAreTheStorys:
    def test_a_switch_flipped_before_the_first_turn_is_kept(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        try:
            assert app.session.story_id is None
            api_stories.update_setting(app.session, "allow_questions", enabled=True)
            app.play("I push the door.")
            assert HOW_TO_ASK in messages(app, "I push the door.")[-1][1]
            stored = app.store.stories.get_settings(app.session.story_id)
            assert [(s.name, s.enabled) for s in stored] == [("allow_questions", True)]
        finally:
            app.close()

    def test_the_switches_survive_a_launch(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        api_stories.update_setting(
            app.session, "allow_questions", enabled=True, position=InjectionPosition(2)
        )
        api_stories.update_setting(app.session, "allow_assistant_notes", display_notes=False)
        app.play("I push the door.")
        app.close()
        app = launch(tmp_path / "state", server)
        try:
            ask = setting(app, "allow_questions")
            assert (ask.enabled, ask.injection_position) == (True, InjectionPosition(2))
            assert setting(app, "allow_assistant_notes").display_notes is False
        finally:
            app.close()

    def test_a_new_story_starts_with_every_setting_off(self, app: App) -> None:
        api_stories.update_setting(app.session, "allow_questions", enabled=True)
        app.play("I push the door.")
        app.play("/new")
        assert not any(each.enabled for each in api_stories.get_settings(app.session))

    def test_a_fork_plays_the_way_its_origin_does(self, app: App) -> None:
        api_stories.update_setting(
            app.session, "allow_assistant_notes", enabled=True, position=InjectionPosition()
        )
        app.play("I push the door.")
        origin = app.session.story_id
        app.play("/fork")
        assert app.session.story_id != origin
        assert app.store.stories.get_settings(app.session.story_id) == (
            app.store.stories.get_settings(origin)
        )
        notes = setting(app, "allow_assistant_notes")
        assert (notes.enabled, notes.injection_position) == (True, InjectionPosition())

    def test_a_story_that_is_not_open_is_reached_by_its_id(self, app: App) -> None:
        first, second = two_stories(app)
        said = api_stories.update_setting(
            app.session, "allow_questions", enabled=True, story_id=first
        )
        assert said
        # The open story is left as it was; the named one took the switch.
        assert not setting(app, "allow_questions").enabled
        elsewhere = api_stories.get_settings(app.session, first).get("allow_questions")
        assert elsewhere is not None and elsewhere.enabled
        assert app.session.story_id == second


class TestToolPrompts:
    """A tool's prompt is prompts.toml's, edited in place from the app:
    the one key rewritten, the pre-edit file kept as a dated backup named
    after the file, and the next request carrying what was saved."""

    def test_a_saved_prompt_lands_in_the_file_and_on_the_wire(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        try:
            api_stories.update_setting(app.session, "allow_questions", enabled=True)
            said = api_settings.set_tool_prompt(app.session, "question", "ASK ANEW")
            assert said
            text = app.paths.prompts_file.read_text()
            assert text.count("tool_questions_prompt = ") == 1 and "ASK ANEW" in text
            assert HOW_TO_ASK not in text
            kept = list(app.paths.config_backups_dir.iterdir())  # the pre-edit file waits
            assert [path.name[: len("prompts-")] for path in kept] == ["prompts-"]
            assert HOW_TO_ASK in kept[0].read_text()
            assert api_settings.get_tool_prompt(app.session, "question") == "ASK ANEW"
            app.play("I push the door.")
            assert messages(app, "I push the door.") == [
                ("user", "((OOC: ASK ANEW))\n\nI push the door.")
            ]
        finally:
            app.close()

    def test_an_emptied_prompt_restores_the_built_in(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        try:
            api_settings.set_tool_prompt(app.session, "question", "  ")
            assert "tool_questions_prompt" not in app.paths.prompts_file.read_text()
            shipped = Prompts().tool_questions_prompt
            assert api_settings.get_tool_prompt(app.session, "question") == shipped
        finally:
            app.close()

    def test_a_name_no_tool_owns_is_refused(self, app: App) -> None:
        with pytest.raises(Refused):
            api_settings.get_tool_prompt(app.session, "plan")
        with pytest.raises(Refused):
            api_settings.set_tool_prompt(app.session, "plan", "x")


class TestSettingsRefused:
    def test_what_a_setting_cannot_take_is_refused(self, app: App) -> None:
        for asked in (
            {"name": "plan", "enabled": True},
            # Past the deepest place. (What is not a position at all —
            # "end", 0, "2" — never becomes one: `InjectionPosition.from_value`
            # reads None at the wire.)
            {"name": "allow_questions", "position": InjectionPosition(9)},
            {"name": "allow_questions", "reminder_text": "Ask about the weather."},
            # Only the notes tool has something to display.
            {"name": "allow_questions", "display_notes": False},
            # A reminder is never the system message's: that is the premise's.
            {"name": "use_story_reminder", "position": InjectionPosition()},
            {"name": "use_shared_reminder", "position": InjectionPosition()},
            # The shared reminder's text is no one story's.
            {"name": "use_shared_reminder", "reminder_text": "Stay grim."},
            # A flag injects nothing, so it rides nowhere.
            {"name": "story_mode", "position": InjectionPosition(1)},
        ):
            with pytest.raises(Refused):
                api_stories.update_setting(app.session, **asked)  # type: ignore[arg-type]
        # Nothing was written by a refusal.
        assert not any(each.enabled for each in api_stories.get_settings(app.session))


def picks(story_id: int, upto: int | None = None, action: str = "resume") -> Picker:
    """A browser stub: the user picked `story_id` — at its last turn, or
    at message `upto` of it with the resume dialog settling `action` —
    and the pick EXECUTES, as the real screen does."""

    def pick(session: Session) -> str | None:
        messages = api_stories.messages_of(session, story_id)
        if not messages:
            return api_stories.land(session, story_id)  # nothing to pick: the story alone
        target = messages[-1] if upto is None else messages[upto - 1]
        return api_stories.land(session, story_id, target.id, action)

    return pick


def two_stories(app: App) -> tuple[int, int]:
    """A titled two-turn story, then a fresh current one. Returns their
    ids (first, current)."""
    app.play("The first story begins.")
    app.play("/title First")
    first = app.session.story_id
    app.play("/new")
    app.play("The second story begins.")
    return first, app.session.story_id


def with_prompt(root, server) -> App:
    """The app over a prompts.toml whose questions prompt is `HOW_TO_ASK`."""
    paths = Paths.resolve(root)
    paths.ensure_tree()
    paths.prompts_file.write_text(f'tool_questions_prompt = "{HOW_TO_ASK}"\n')
    return launch(root, server)


def messages(app: App, last_line: str) -> list[tuple[str, str]]:
    """The turn's request as (role, text) pairs — picked by its newest
    line, since a background request may have landed after it."""
    request = scripted.chat_request(app.server, last_line)
    return [(m["role"], scripted.content_text(m)) for m in request["messages"]]


def setting(app: App, name: str) -> StorySetting:
    """One setting of the open story, as it stands."""
    found = api_stories.get_settings(app.session).get(name)
    assert found is not None
    return found
