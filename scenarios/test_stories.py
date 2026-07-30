"""Managing stories: browsing and resuming, forking, /rename, /system, /new.

The browser itself is a tui surface tested in its own file; here it is an
injected pick, so these stories are about what a selection MEANS — resume,
the fork question, the head rewind — and about the story commands' effects
on the store, the session, and the remembered state.
"""

import builtins
from collections.abc import Callable

import pytest

from otaku.chat.session import TUI, PickedStory
from otaku.store import Store
from otaku.store.stories import StoryListing
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config

Picker = Callable[[Store, list[StoryListing], int | None], PickedStory | None]


def picks(story_id: int, upto: int | None = None) -> Picker:
    """A browser stub: the user picked `story_id` — at its last turn, or at
    message `upto` of it."""

    def pick(store: Store, rows: list[StoryListing], current: int | None) -> PickedStory:
        messages = store.stories.get_messages(story_id)
        cut = messages if upto is None else messages[:upto]
        return (story_id, cut, len(messages))

    return pick


def two_stories(app: App) -> tuple[int, int]:
    """A titled two-turn story, then a fresh current one. Returns their
    ids (first, current)."""
    app.play("The first story begins.")
    app.play("/rename First")
    first = app.session.story_id
    app.play("/new")
    app.play("The second story begins.")
    return first, app.session.story_id


class TestBrowsing:
    def test_no_stories_yet(self, app: App, capsys) -> None:
        app.play("/stories")
        assert "No saved stories yet." in capsys.readouterr().out

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


class TestForkQuestion:
    """Picking an EARLIER message asks: fork here? Yes copies, no rewinds,
    anything else cancels."""

    def ask(self, app: App, story_id: int, answer: str, monkeypatch) -> None:
        app.session.tui = TUI(pick_story=picks(story_id, upto=1))
        monkeypatch.setattr(builtins, "input", lambda prompt="": answer)
        app.play("/stories")

    # The empty answer is the [Y/n] default; "н" is y on the ЙЦУКЕН layout.
    @pytest.mark.parametrize("answer", ["", "y", "н"])
    def test_yes_forks_at_the_picked_message(
        self, app: App, capsys, monkeypatch, answer: str
    ) -> None:
        first, second = two_stories(app)
        old_head = app.store.stories.get_head(first)
        self.ask(app, first, answer, monkeypatch)

        out = capsys.readouterr().out
        assert "Forked to 'First - 2'." in out
        fork = app.session.story_id
        assert fork not in (first, second)
        assert app.store.stories.get(fork).forked_from_id == first  # lineage, for the record
        assert [m.body for m in app.session.messages] == ["The first story begins."]
        # The copy is deep: its message is its own row, not a shared one.
        assert app.session.messages[0].id != app.store.stories.get_messages(first)[0].id
        # The original did not move.
        assert app.store.stories.get_head(first) == old_head

    def test_no_rewinds_the_head_keeping_siblings(self, app: App, capsys, monkeypatch) -> None:
        first, _ = two_stories(app)
        reply_id = app.store.stories.get_messages(first)[1].id
        self.ask(app, first, "n", monkeypatch)

        assert "Resuming here — later messages stay in the tree as siblings." in (
            capsys.readouterr().out
        )
        assert app.session.story_id == first
        assert [m.body for m in app.session.messages] == ["The first story begins."]
        # The abandoned reply still exists in the tree — nothing was deleted.
        assert app.store.messages.count_body_chars([reply_id]) > 0

    def test_any_other_answer_cancels(self, app: App, capsys, monkeypatch) -> None:
        first, second = two_stories(app)
        self.ask(app, first, "what?", monkeypatch)
        assert "Cancelled." in capsys.readouterr().out
        assert app.session.story_id == second  # still where the user was
        assert app.store.stories.get_head(first) is not None

    def test_ctrl_c_at_the_question_cancels(self, app: App, capsys, monkeypatch) -> None:
        first, second = two_stories(app)
        app.session.tui = TUI(pick_story=picks(first, upto=1))

        def interrupted(prompt: str = "") -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr(builtins, "input", interrupted)
        app.play("/stories")
        assert "Cancelled." in capsys.readouterr().out
        assert app.session.story_id == second


class TestFork:
    def test_nothing_to_fork_yet(self, app: App, capsys) -> None:
        app.play("/fork")
        assert "Nothing to fork yet — send a message first." in capsys.readouterr().out

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
        assert "Forked." in capsys.readouterr().out
        assert app.store.stories.get(app.session.story_id).title == ""

    def test_forks_of_a_titled_story_number_themselves(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/rename Hall")
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

    def test_with_no_settle_margin_the_memory_survives_the_fork(self, server, tmp_path) -> None:
        # settle_messages = 0: a scene ending at the head is still "settled",
        # so the fork carries the whole memory — scene, journals, cast.
        set_config(tmp_path / "state", settle_messages=0)
        app = launch(tmp_path / "state", server)
        try:
            for i in range(3):
                app.play(f"Turn number {i}.")
            app.play("/extract")
            app.play("/fork")
            story_id = app.session.story_id
            ids = app.store.stories.get_messages_ids(story_id)
            scenes = app.store.scenes.get_current(story_id, ids)
            assert len(scenes) == 1
            assert scenes[0].history == scripted.STORY_SO_FAR
            cast = app.store.characters.list(story_id)
            assert [c.name for c in cast] == ["Keeper"]
            assert app.store.journals.get_current(story_id)[cast[0].id].state == "at the gate"
        finally:
            app.close()

    def test_the_settle_margin_holds_a_fresh_scene_back(self, app: App) -> None:
        # Default settle (20): a scene ending near the head is NOT copied —
        # the live rule "no scene ends where the story is still moving"
        # holds in the copy; its span becomes unextracted tail instead.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/fork")
        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        assert app.store.scenes.get_current(story_id, ids) == []
        # The cast is per-story and always travels.
        assert [c.name for c in app.store.characters.list(story_id)] == ["Keeper"]


class TestRename:
    def test_rename_titles_the_story(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/rename The Throne Hall")
        assert 'Renamed to "The Throne Hall".' in capsys.readouterr().out
        assert app.store.stories.get(app.session.story_id).title == "The Throne Hall"

    def test_bare_rename_shows_the_title_or_usage(self, app: App, capsys) -> None:
        app.play("/rename")
        assert "Usage: /rename NEW-TITLE" in capsys.readouterr().out
        app.play("I enter the hall.")
        app.play("/rename Hall")
        capsys.readouterr()
        app.play("/rename")
        assert 'Title: "Hall"' in capsys.readouterr().out

    def test_rename_before_the_first_turn_creates_the_story(self, app: App) -> None:
        assert app.session.story_id is None
        app.play("/rename The Planned Story")
        story_id = app.session.story_id
        assert story_id is not None
        assert app.store.stories.get(story_id).title == "The Planned Story"
        app.play("I enter the hall.")  # the first turn lands in that same story
        assert app.session.story_id == story_id

    def test_rename_does_not_reorder_the_story_list(self, app: App) -> None:
        first, second = two_stories(app)
        app.session.tui = TUI(pick_story=picks(first))
        app.play("/stories")
        app.play("/rename Renamed Later")
        # Titling is metadata: the list still leads with the recently
        # PLAYED story, not the recently renamed one.
        assert app.store.stories.list()[0].id == second


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

    def test_bare_system_shows_the_prompt_or_none(self, app: App, capsys) -> None:
        app.play("/system")
        assert "System: (none)" in capsys.readouterr().out
        app.play("/system You are the narrator.")
        capsys.readouterr()
        app.play("/system")
        assert 'System: "You are the narrator."' in capsys.readouterr().out


class TestNew:
    def test_new_detaches_and_the_next_turn_starts_fresh(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        original = app.session.story_id
        app.play("/new")
        assert "Started a new story." in capsys.readouterr().out
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
