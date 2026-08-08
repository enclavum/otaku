"""Managing stories: browsing and resuming, forking, /title, /system, /new.

The browser itself is a tui surface tested in its own file; here it is an
injected pick, so these stories are about what a settled selection MEANS —
resume, fork, truncate — and about the story commands' effects on the
store, the session, and the remembered state.
"""

from collections.abc import Callable

from otaku.chat.session import TUI, PickedStory
from otaku.store import Store
from otaku.store.stories import StoryListing
from otaku.tui import stories
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch
from scenarios.support.screens import CTRL_S, DELETE, ENTER, ESC, run_screen

Picker = Callable[[Store, list[StoryListing], int | None], PickedStory | None]


class TestBrowsing:
    def test_picking_a_story_resumes_it(self, app: App, capsys) -> None:
        first, _second = two_stories(app)
        app.play("/system The second premise.")
        app.session.tui = TUI(pick_story=picks(first))
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

    def test_a_cancelled_browse_rereads_an_edited_story(self, app: App) -> None:
        app.play("I enter the hall.")
        first_id = app.session.messages[0].id
        # The browser edits a message in place, then closes with no pick.
        app.store.messages.update(first_id, "I enter the throne hall.")
        app.session.tui = TUI(pick_story=lambda store, rows, current: None)
        app.play("/stories")
        assert app.session.messages[0].body == "I enter the throne hall."


class TestResumeDialog:
    """Picking an EARLIER message: the browser's dialog settled the action —
    the command executes a fork or a truncation (cancel never leaves the
    browser)."""

    def decided(self, app: App, story_id: int, action: str) -> None:
        app.session.tui = TUI(pick_story=picks(story_id, upto=1, action=action))
        app.play("/stories")

    def test_fork_copies_at_the_picked_message(self, app: App, capsys) -> None:
        first, second = two_stories(app)
        old_head = app.store.stories.get_head(first)
        self.decided(app, first, "fork")

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

    def test_truncate_rewinds_the_head_keeping_siblings(self, app: App, capsys) -> None:
        first, _ = two_stories(app)
        reply_id = app.store.stories.get_messages(first)[1].id
        self.decided(app, first, "truncate")

        assert "Truncated at message 1." in capsys.readouterr().out
        assert app.session.story_id == first
        assert [m.body for m in app.session.messages] == ["The first story begins."]
        # The abandoned reply still exists in the tree — nothing was deleted.
        assert app.store.messages.count_body_chars([reply_id]) > 0


class TestStoryBrowser:
    def pick(self, app: App, keys: str, initial: int | None = None):
        rows = app.store.stories.list()
        return run_screen(keys, lambda: stories.pick(app.store, rows, initial))

    def test_e_edits_a_message_in_place(self, app: App) -> None:
        _first, second = two_stories(app)
        assert self.pick(app, ENTER + "e" + "!" + CTRL_S + ESC + ESC) is None
        chain = app.store.stories.get_messages(second)
        assert chain[-1].body == scripted.CHAT_REPLY + "!"

    def test_delete_removes_a_story_after_a_confirm(self, app: App) -> None:
        first, _second = two_stories(app)
        assert self.pick(app, DELETE + "y" + ESC) is None
        remaining = [row.id for row in app.store.stories.list()]
        assert remaining == [first]  # the newest row was deleted


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

    def test_forks_of_a_titled_story_number_themselves(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/title Hall")
        origin = app.session.story_id
        app.play("/fork")
        assert app.store.stories.get(app.session.story_id).title == "Hall - 2"
        app.session.tui = TUI(pick_story=picks(origin))
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

    def test_title_does_not_reorder_the_story_list(self, app: App) -> None:
        first, second = two_stories(app)
        app.session.tui = TUI(pick_story=picks(first))
        app.play("/stories")
        app.play("/title Renamed Later")
        # Titling is metadata: the list still leads with the recently
        # PLAYED story, not the recently renamed one.
        assert app.store.stories.list()[0].id == second


class TestNew:
    def test_new_detaches_and_the_next_turn_starts_fresh(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        original = app.session.story_id
        app.play("/new")
        out = capsys.readouterr().out
        assert "Started a new story." in out
        assert app.session.story_id is None
        assert app.session.messages == []
        # A relaunch starts fresh too, not back in the left story.
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.story_id is None
        relaunched.close()

        app.play("A different beginning.")
        assert app.session.story_id != original
        # The left story is intact, ready to be resumed from the browser.
        assert len(app.store.stories.get_messages(original)) == 2


def picks(story_id: int, upto: int | None = None, action: str = "resume") -> Picker:
    """A browser stub: the user picked `story_id` — at its last turn, or at
    message `upto` of it with the resume dialog settling `action`."""

    def pick(store: Store, rows: list[StoryListing], current: int | None) -> PickedStory:
        messages = store.stories.get_messages(story_id)
        cut = messages if upto is None else messages[:upto]
        return (story_id, cut, action)

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
