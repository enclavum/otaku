"""Managing stories: browsing and resuming, forking, /title, /system, /new —
and the actions on one: /undo takes the trailing exchange back, /regen
sets the reply aside for a fresh take.

The browser itself is a full-screen surface driven headless below; for
the routing stories it is a stubbed `screens.stories.pick` — patched at
its module seam — that EXECUTES the settled selection through
`backend.api`, exactly as the real screen does (the screens' one
ownership rule), and hands back the story it settled in for the chat to
land. These stories are about what a settled selection MEANS — resume,
fork, truncate — and about the story commands' effects on the store, the
story the chat plays, and the remembered state.
"""

from collections.abc import Callable

from otaku.backend.api import Api, Layout
from otaku.backend.domain.app._paths import Paths
from otaku.terminal.api.story.prompts import read_prompt
from otaku.terminal.screens import stories as screen_stories
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config
from scenarios.support.screens import (
    BACKSPACE,
    CTRL_S,
    DELETE,
    DOWN,
    ENTER,
    ESC,
    LEFT,
    RIGHT,
    run_screen,
)

Picker = Callable[[Api, "int | None"], "tuple[int, str] | None"]

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
        assert app.story_id == first
        assert app.premise == ""  # the second story's premise stayed behind
        assert [m.body for m in app.messages] == [
            "The first story begins.",
            scripted.CHAT_REPLY,
        ]
        # The resume is remembered: a bare relaunch lands in that story.
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.story_id == first
        relaunched.close()

    def test_an_edit_in_a_cancelled_browse_reaches_the_story(self, app: App, monkeypatch) -> None:
        app.play("I enter the hall.")
        first_id = app.messages[0].id

        # The browser edits a message in place, then closes with no pick —
        # the edit goes through the api, and the chat reads its story again.
        def edit_and_cancel(api: Api, played: int | None) -> None:
            assert played is not None
            api.stories.messages.update(played, first_id, body="I enter the throne hall.")
            return None

        monkeypatch.setattr(screen_stories, "pick", edit_and_cancel)
        app.play("/stories")
        assert app.messages[0].body == "I enter the throne hall."


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
        fork = app.story_id
        assert fork not in (first, second)
        assert app.store.stories.get(fork).forked_from_id == first  # lineage, for the record
        assert [m.body for m in app.messages] == ["The first story begins."]
        # The copy is deep: its message is its own row, not a shared one.
        assert app.messages[0].id != app.store.stories.get_messages(first)[0].id
        # The original did not move.
        assert app.store.stories.get_head(first) == old_head

    def test_truncate_rewinds_the_head_keeping_siblings(
        self, app: App, capsys, monkeypatch
    ) -> None:
        first, _ = two_stories(app)
        reply_id = app.store.stories.get_messages(first)[1].id
        self.decided(app, monkeypatch, first, "truncate")

        assert "Truncated at message 1." in capsys.readouterr().out
        assert app.story_id == first
        assert [m.body for m in app.messages] == ["The first story begins."]
        # The abandoned reply still exists in the tree — nothing was deleted.
        assert app.store.messages.count_body_chars([reply_id]) > 0


class TestStoryBrowser:
    def pick(self, app: App, keys: str) -> None:
        """The browser, driven by `keys` through /stories as typed — the
        chat landing where it settles."""
        run_screen(keys, lambda: app.play("/stories"))

    def test_enter_on_a_story_with_nothing_played_resumes_it(self, app: App) -> None:
        # Nothing played means nothing to pick, and the dossier's messages
        # tab would open on nothing with no way to land: Enter on the row
        # resumes the story itself, so a story started and left is not
        # one that can only be deleted.
        app.play("I enter the hall.")
        app.play("/title Played")
        played = app.story_id
        app.play("/new Blank")
        blank = app.story_id
        self.pick(app, "/Played" + ENTER + ENTER)
        assert app.story_id == played
        self.pick(app, "/Blank" + ENTER)
        assert app.story_id == blank
        assert app.messages == []
        app.play("A different beginning.")
        assert app.story_id == blank  # the turn lands in it
        assert len(app.store.stories.get_messages(blank)) == 2

    def test_e_edits_a_message_in_place(self, app: App) -> None:
        _first, second = two_stories(app)
        self.pick(app, ENTER + "e" + "!" + CTRL_S + ESC + ESC)
        chain = app.store.stories.get_messages(second)
        assert chain[-1].body == "!" + scripted.CHAT_REPLY

    def test_the_dossier_edits_another_story_s_premise(self, app: App) -> None:
        # Any story opens whole and any story EDITS: drilled into the
        # row under the cursor — not the open one — the premise tab's
        # save lands on that story, and the open story keeps its own.
        first, _second = two_stories(app)
        self.pick(app, DOWN + ENTER + LEFT + ENTER + "!" + CTRL_S + ESC + ESC)
        assert app.store.stories.get_system(first) == "!"
        assert app.premise == ""  # the open story's premise is untouched

    def test_the_dossier_clears_a_premise(self, app: App) -> None:
        # An emptied editor is a save too: the premise is removed, not
        # reported — the trap this covers is "" falling through to the
        # report and the clear silently not landing.
        first, _second = two_stories(app)
        self.pick(app, DOWN + ENTER + LEFT + ENTER + "!" + CTRL_S + ESC + ESC)
        assert app.store.stories.get_system(first) == "!"
        keys = DOWN + ENTER + LEFT + ENTER + DELETE + CTRL_S + ESC + ESC
        self.pick(app, keys)
        assert app.store.stories.get_system(first) == ""

    def test_the_dossier_edits_another_story_s_message(self, app: App) -> None:
        first, _second = two_stories(app)
        self.pick(app, DOWN + ENTER + "e" + "!" + CTRL_S + ESC + ESC)
        assert app.store.stories.get_messages(first)[-1].body == "!" + scripted.CHAT_REPLY

    def test_the_dossier_edits_another_story_s_lore(self, app: App) -> None:
        # The scenes tab of the story under the cursor — the write is
        # addressed to THAT story and passes its ownership check.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        first = app.story_id
        app.play("/new")
        app.play("The second story begins.")
        keys = DOWN + ENTER + RIGHT + ENTER + DOWN + ENTER + "!" + CTRL_S + ESC * 3
        self.pick(app, keys)
        ids = app.store.stories.get_messages_ids(first)
        scene = app.store.scenes.get_current(first, ids)[0]
        assert scene.summary == "!A guest came in and met the Keeper."

    def test_delete_removes_a_story_after_a_confirm(self, app: App) -> None:
        first, _second = two_stories(app)
        self.pick(app, DELETE + "y" + ESC)
        remaining = [row.id for row in app.store.stories.list()]
        assert remaining == [first]  # the newest row was deleted

    def test_the_mac_delete_key_deletes_too(self, app: App) -> None:
        # macOS captions its backspace key "delete" — with no filter open
        # it must mean what it says.
        first, _second = two_stories(app)
        self.pick(app, BACKSPACE + "y" + ESC)
        assert [row.id for row in app.store.stories.list()] == [first]

    def test_backspace_inside_the_filter_never_deletes(self, app: App) -> None:
        # While a filter is open backspace edits it, so the `y` lands in
        # the query — no confirm ever came up, and no story goes anywhere.
        first, second = two_stories(app)
        self.pick(app, "/x" + BACKSPACE + "y" + ESC + ESC)
        assert [row.id for row in app.store.stories.list()] == [second, first]


class TestFork:
    def test_fork_switches_to_the_copy_and_leaves_the_original(self, app: App) -> None:
        app.play("I enter the hall.")
        original = app.story_id
        original_ids = [m.id for m in app.messages]
        app.play("/fork")

        fork = app.story_id
        assert fork != original
        assert [m.body for m in app.messages] == ["I enter the hall.", scripted.CHAT_REPLY]
        assert [m.id for m in app.messages] != original_ids  # fresh rows
        assert [m.body for m in app.store.stories.get_messages(original)] == [
            "I enter the hall.",
            scripted.CHAT_REPLY,
        ]
        # The fork is what a relaunch resumes now.
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.story_id == fork
        relaunched.close()

    def test_a_fork_keeps_who_made_its_memory(self, app: App) -> None:
        # The copy's scenes, journals and cast are the origin's rows, so
        # the model that made each travels with it.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/fork")
        fork = app.story_id
        store = app.store
        rows = (*store.scenes.list(fork), *store.characters.list(fork), *store.journals.list(fork))
        assert rows
        assert {(row.provider, row.model) for row in rows} == {("generic", "test-model")}

    def test_an_untitled_story_forks_untitled(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/fork")
        # No title to inherit: the copy is named by its opening line, the
        # way every untitled story is named.
        assert "Forked to: I enter the hall." in capsys.readouterr().out
        assert app.store.stories.get(app.story_id).title == ""

    def test_forks_of_a_titled_story_number_themselves(self, app: App, capsys, monkeypatch) -> None:
        app.play("I enter the hall.")
        app.play("/title Hall")
        origin = app.story_id
        app.play("/fork")
        assert app.store.stories.get(app.story_id).title == "Hall - 2"
        monkeypatch.setattr(screen_stories, "pick", picks(origin))
        app.play("/stories")  # back to the origin, then fork again
        app.play("/fork")
        assert app.store.stories.get(app.story_id).title == "Hall - 3"

    def test_an_explicit_title_is_used_verbatim(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/fork Another door")
        assert app.store.stories.get(app.story_id).title == "Another door"

    def test_the_memory_travels_with_the_copy(self, app: App) -> None:
        # A branch is the story so far, memory included — whatever the
        # settle margin is. A 3-turn story is shorter than the default 20,
        # which used to leave every scene behind and the fork blank.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/fork")
        story_id = app.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        scenes = app.store.scenes.get_current(story_id, ids)
        assert len(scenes) == 1
        assert scenes[0].history == "A guest came in and met the Keeper."
        cast = app.store.characters.list(story_id)
        assert [c.name for c in cast] == ["Keeper"]
        assert app.store.journals.get_current(story_id, ids)[cast[0].id].state == "at the gate"


class TestSystem:
    def test_system_with_no_story_makes_one_and_plays_on_it(self, app: App) -> None:
        app.play("/system You are the narrator.")
        made = app.story_id
        assert made is not None  # a premise saved needs its story
        assert app.store.stories.get_system(made) == "You are the narrator."
        app.play("I enter the hall.")
        assert app.story_id == made
        assert app.messages[0].body == "I enter the hall."  # played on the story /system made

    def test_clearing_with_no_story_makes_none(self, app: App) -> None:
        app.play("/system -")
        assert app.story_id is None
        assert app.store.stories.list() == []

    def test_system_on_a_live_story_persists(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/system Answer briefly.")
        assert "System prompt set (15 chars)." in capsys.readouterr().out
        assert app.store.stories.get_system(app.story_id) == "Answer briefly."

    def test_a_bare_system_reports_and_changes_nothing(self, app: App, capsys) -> None:
        app.play("/system Answer briefly.")
        capsys.readouterr()
        app.play("/system")
        assert "Answer briefly." in capsys.readouterr().out  # the premise, reported
        assert app.premise == "Answer briefly."

    def test_a_dash_clears_the_premise(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/system Answer briefly.")
        app.play("/system -")
        assert app.premise == ""
        assert app.store.stories.get_system(app.story_id) == ""
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
        assert app.store.stories.get_system(app.story_id) == premise.strip()
        assert "Traceback" not in capsys.readouterr().out

    def test_a_multiline_premise_survives_whole(self, app: App) -> None:
        # What a `/system """…"""` block collects: newlines and all, and
        # no stray delimiters in the stored text.
        premise = "## Premise\n\nA quiet story.\n- one rule\n- another"
        app.play(f"/system {premise}")
        app.play("I enter the hall.")
        assert app.store.stories.get_system(app.story_id) == premise

    def test_a_file_argument_supplies_the_prompt(self, app: App, tmp_path) -> None:
        premise = tmp_path / "premise.md"
        premise.write_text("You are the narrator.\n", encoding="utf-8")
        app.play(f"/system {premise}")
        app.play("I enter the hall.")
        assert app.store.stories.get_system(app.story_id) == "You are the narrator."

    def test_the_completion_trigger_is_not_part_of_the_name(self, app: App, tmp_path) -> None:
        premise = tmp_path / "premise.md"
        premise.write_text("You are the narrator.", encoding="utf-8")
        app.play(f"/system @{premise}")
        assert app.premise == "You are the narrator."

    def test_text_naming_no_file_stays_literal(self, app: App) -> None:
        app.play("/system /nowhere/gone.md")
        assert app.premise == "/nowhere/gone.md"

    def test_an_unreadable_file_changes_nothing(self, app: App, capsys, tmp_path) -> None:
        sealed = tmp_path / "sealed.md"
        sealed.write_text("You are the narrator.", encoding="utf-8")
        sealed.chmod(0o000)
        app.play("/system The premise stands.")
        app.play(f"/system {sealed}")
        assert "Could not read" in capsys.readouterr().out
        assert app.premise == "The premise stands."

    def test_an_empty_file_changes_nothing(self, app: App, capsys, tmp_path) -> None:
        empty = tmp_path / "empty.md"
        empty.write_text("", encoding="utf-8")
        app.play("/system The premise stands.")
        app.play(f"/system {empty}")
        assert "empty — system prompt unchanged" in capsys.readouterr().out
        assert app.premise == "The premise stands."


class TestTitle:
    def test_title_titles_the_story(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/title The Throne Hall")
        assert 'Story title set to "The Throne Hall".' in capsys.readouterr().out
        assert app.store.stories.get(app.story_id).title == "The Throne Hall"

    def test_title_before_the_first_turn_creates_the_story(self, app: App) -> None:
        assert app.story_id is None
        app.play("/title The Planned Story")
        story_id = app.story_id
        assert story_id is not None
        assert app.store.stories.get(story_id).title == "The Planned Story"
        app.play("I enter the hall.")  # the first turn lands in that same story
        assert app.story_id == story_id

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
        original = app.story_id
        app.play("/new")
        assert "Started a new story." in capsys.readouterr().out
        started = app.story_id
        assert started is not None and started != original
        # Browsable before its first turn — the reason it is created here
        # and not at the turn.
        assert started in {row.id for row in app.store.stories.list()}
        assert app.messages == []

        app.play("A different beginning.")
        assert app.story_id == started  # the turn lands in it, not in another
        # The left story is intact, ready to be resumed from the browser.
        assert len(app.store.stories.get_messages(original)) == 2

    def test_the_started_story_is_where_a_relaunch_lands(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/new")
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.story_id == app.story_id
        relaunched.close()

    def test_new_names_the_story_it_starts(self, app: App, capsys) -> None:
        app.play("/new The Second Tale")
        assert "The Second Tale" in capsys.readouterr().out
        assert app.store.stories.get(app.story_id).title == "The Second Tale"
        # The name is on the row, so /title and the browser read it from
        # the one place a title lives.
        assert app.store.stories.list()[0].label == "The Second Tale"


class TestUndo:
    def test_undo_discards_the_exchange_but_keeps_it_in_the_tree(self, app: App) -> None:
        app.play("The first turn.")
        app.play("The second turn.")
        app.play("/undo")
        chain = app.store.stories.get_messages(app.story_id)
        assert [m.body for m in chain] == ["The first turn.", scripted.CHAT_REPLY]
        # Nothing was deleted: the undone turns stay as siblings.
        assert [m.id for m in app.messages] == [m.id for m in chain]
        assert app.store.messages.get_parent(3) == 2

    def test_undo_never_swallows_sequential_user_messages(self, app: App, tmp_path) -> None:
        # Sequential user rows are story, not one submission — /undo takes
        # back the played exchange only. A text import is how a run of
        # user rows arises through the surface.
        tale = tmp_path / "tale.txt"
        tale.write_text("First beat.\n\nSecond beat.\n\nThird beat.", encoding="utf-8")
        app.play(f"/import {tale}")
        imported = [m.body for m in app.messages]
        app.play("I look around.")
        app.play("/undo")
        assert [m.body for m in app.messages] == imported


class TestRegenerate:
    def test_regen_replaces_the_reply_and_siblings_the_old_one(self, app: App) -> None:
        app.play("I roll the dice.")
        app.server.script = lambda body: "It came up six."
        app.play("/regen")
        chain = app.store.stories.get_messages(app.story_id)
        assert [m.body for m in chain] == ["I roll the dice.", "It came up six."]
        # Both replies hang off the same prompt: the old one is a sibling.
        assert app.store.messages.get_parent(2) == app.store.messages.get_parent(3) == 1

    def test_regen_resends_the_prompt_a_failure_left_unanswered(self, app: App) -> None:
        # A request that dies before any content records no reply, so the
        # prompt stands alone at the end of the story: /regen sends it again.
        app.server.fail_after = 0
        app.play("I roll the dice.")
        assert [m.role for m in app.messages] == ["user"]
        app.server.fail_after = None
        app.play("/regen")
        chain = app.store.stories.get_messages(app.story_id)
        assert [m.body for m in chain] == ["I roll the dice.", scripted.CHAT_REPLY]
        assert app.server.requests[-1]["messages"][-1]["content"] == "I roll the dice."

    def test_regen_without_a_model_touches_nothing(self, server, tmp_path, capsys) -> None:
        # The guard runs before the drop and the erase: the reply stays
        # in the chain and on the screen; only the hint prints.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec=None)
        try:
            capsys.readouterr()
            before = [m.id for m in app.messages]
            app.play("/regen")
            out = capsys.readouterr().out
            assert "No model selected" in out
            assert "Nothing to regenerate" not in out
            assert [m.id for m in app.messages] == before
        finally:
            app.close()


class TestTheStoryTheTerminalHolds:
    """The app holds no story: the terminal does — made by the first write
    that lands, none left behind by one that is refused, and read again
    before each prompt, so a story moved on or deleted elsewhere (another
    tab, a second terminal) is played from where it now stands."""

    def test_a_refused_first_line_leaves_no_story_and_keeps_what_was_set(self, app: App) -> None:
        app.play("/set mode story")
        assert app.store.stories.list() == []  # a layout set makes no story
        app.play("/you")  # refused before anything lands
        assert app.store.stories.list() == []
        assert app.layout is Layout.CANVAS
        app.play("I climb the stairs.")
        (made,) = app.store.stories.list()
        assert app.api.stories.get(made.id).layout is Layout.CANVAS

    def test_a_line_after_the_story_moved_elsewhere_goes_on_from_where_it_stands(
        self, app: App
    ) -> None:
        app.play("I climb the stairs.")
        story = app.ensure_story()
        # another client plays on the same story
        list(
            app.api.stories.messages.create(
                story, line="Meanwhile, the lamp gutters.", expected_head=app.head(story)
            )
        )
        elsewhere = app.messages[-1].id
        app.play("I trim the wick.")
        mine = next(m for m in app.messages if m.body == "I trim the wick.")
        assert app.store.messages.get_parent(mine.id) == elsewhere

    def test_a_story_deleted_elsewhere_is_let_go_and_the_next_line_makes_another(
        self, app: App
    ) -> None:
        app.play("I climb the stairs.")
        gone = app.ensure_story()
        app.api.stories.delete(gone)
        app.play("I start again.")
        assert app.story_id not in (None, gone)
        assert app.messages[0].body == "I start again."


def prompt_of(app: App, name: str) -> str:
    """A setting's prompt as the file holds it, reached as both frontends
    reach it — its tool's, or the one it names (`read_prompt`)."""
    return read_prompt(app.api, app.api.stories.settings.get(app.ensure_story(), name))


def picks(story_id: int, upto: int | None = None, action: str = "resume") -> Picker:
    """A browser stub: the user picked `story_id` — at its last turn, or
    at message `upto` of it with the resume dialog settling `action` —
    and the pick EXECUTES, as the real screen does, handing back the story
    it settled in and its line."""

    def pick(api: Api, played: int | None) -> tuple[int, str] | None:
        messages = api.stories.messages.list(story_id)
        if not messages or upto is None or action == "resume":
            landed = api.stories.get(story_id)  # the story as it stands
            return landed.id, landed.landed_line()
        target = messages[upto - 1]
        if action == "fork":
            cut = api.stories.forks.create(story_id, at=target.id)
        else:
            cut = api.stories.update(
                story_id, head=target.id, expected_head=api.stories.get(story_id).head
            )
        return cut.value.id, cut.notice

    return pick


def two_stories(app: App) -> tuple[int, int]:
    """A titled two-turn story, then a fresh current one. Returns their
    ids (first, current)."""
    app.play("The first story begins.")
    app.play("/title First")
    first = app.story_id
    app.play("/new")
    app.play("The second story begins.")
    return first, app.story_id


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
