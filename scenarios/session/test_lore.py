"""The lore engine and its commands: the /lore dossier edits the story
in place (premise, messages, scenes, cast), /extract closes scenes over
played messages and threads each character's memory forward, the closed
middle reaches the wire as summaries, and /merge folds extraction
duplicates."""

import contextlib
import json
import signal
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from otaku.backend.api import lore as api_lore
from otaku.backend.formats import exports, imports
from otaku.backend.paths import Paths
from otaku.backend.session import Refused
from otaku.terminal.screens import story as screen_story
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config, set_config_provider
from scenarios.support.screens import CTRL_S, DOWN, ENTER, ESC, SHIFT_TAB, TAB, run_screen
from scenarios.support.server import numbered_script

CHAPEL = Path(__file__).parent.parent / "fixtures" / "chapel.md"
SERAPHINA = Path(__file__).parent.parent / "fixtures" / "seraphina.png"


class TestLoreBrowser:
    def test_a_scene_title_edits_in_place(self, app: App) -> None:
        story_id = remembered(app)
        calls_before = lore_calls(app)
        browse(app, story_id, ENTER + ENTER + "!" + CTRL_S + ESC * 3)
        ids = app.store.stories.get_messages_ids(story_id)
        scene = app.store.scenes.get_current(story_id, ids)[0]
        assert scene.title == "!The Meeting"  # typed at the start — the editor opens there
        assert lore_calls(app) == calls_before  # the browser never calls a model

    def test_an_emptied_summary_is_refused(self, app: App) -> None:
        # A cleared summary would silently swallow the scene's span from
        # every future prompt — the browser refuses it, like the story
        # editor refuses an emptied message.
        story_id = remembered(app)
        wipe = "\x7f" * len("A guest came in and met the Keeper.")
        browse(app, story_id, ENTER + DOWN + ENTER + wipe + CTRL_S + ESC * 3)
        ids = app.store.stories.get_messages_ids(story_id)
        scene = app.store.scenes.get_current(story_id, ids)[0]
        assert scene.summary == "A guest came in and met the Keeper."

    def test_the_cast_lens_edits_a_description(self, app: App) -> None:
        story_id = remembered(app)
        browse(app, story_id, TAB + ENTER + ENTER + "!" + CTRL_S + ESC * 3)
        keeper = app.store.characters.list(story_id)[0]
        assert (
            keeper.description == "!warden of the gate"
        )  # typed at the start — the editor opens there

    def test_a_journal_entry_edit_invalidates_the_derived_history(self, app: App) -> None:
        # Fix the input, and the output follows: editing an entry clears
        # the character's rolled-up history, and the next pass rebuilds it.
        # (The entry is the FOURTH field row — title, summary, the
        # scene's history, then the journal.)
        story_id = remembered(app)
        keeper = app.store.characters.list(story_id)[0]
        browse(app, story_id, ENTER + DOWN * 3 + ENTER + "!" + CTRL_S + ESC * 3)
        journal = app.store.journals.list(story_id)[-1]
        assert journal.entry == "!I saw the guest."  # typed at the start — the editor opens there
        assert not journal.history  # invalidated with the edit
        app.play("/extract")  # declines a new scene, heals the rollup
        ids = app.store.stories.get_messages_ids(story_id)
        memory = app.store.journals.get_current(story_id, ids)[keeper.id]
        assert memory.history  # rebuilt from the fixed input

    def test_the_pivot_reaches_the_same_journal_through_the_other_tab(self, app: App) -> None:
        # `o` on a scene's journal entry lands on the SAME row seen from
        # the character's side — and an edit there addresses that row,
        # not whatever the other tab's cursor last pointed at.
        story_id = remembered(app)
        browse(app, story_id, ENTER + DOWN * 3 + "o" + ENTER + "!" + CTRL_S + ESC * 3)
        assert app.store.journals.list(story_id)[-1].entry == "!I saw the guest."

    def test_the_scene_history_field_is_the_extractor_s_own(self, app: App) -> None:
        # The row after the summary is the story so far through the
        # scene — shown in every scene, never editable: Enter refuses to
        # open it, so the typed correction lands nowhere.
        story_id = remembered(app)
        ids = app.store.stories.get_messages_ids(story_id)
        before = app.store.scenes.get_current(story_id, ids)[0].history
        assert before  # written when the scene closed
        browse(app, story_id, ENTER + DOWN * 2 + ENTER + "!" + CTRL_S + ESC * 3)
        assert app.store.scenes.get_current(story_id, ids)[0].history == before

    def test_a_state_is_the_extractor_s_own(self, app: App) -> None:
        # A state is superseded by the next scene's row, so no hand
        # corrects it — the editor never opens and the store never moves.
        story_id = remembered(app)
        before = app.store.journals.list(story_id)[-1].state
        browse(app, story_id, ENTER + DOWN * 4 + ENTER + "!" + CTRL_S + ESC * 3)
        assert app.store.journals.list(story_id)[-1].state == before

    def test_the_premise_tab_writes_the_system_prompt(self, app: App) -> None:
        # Two tabs back from the scenes sits the premise, edited in
        # place. It is the same field /system sets, and this is the open
        # story — so the session follows the store.
        story_id = remembered(app)
        browse(app, story_id, SHIFT_TAB * 2 + ENTER + "!" + CTRL_S + ESC * 2)
        assert app.store.stories.get_system(story_id) == "!"
        assert app.session.system == "!"

    def test_a_landing_in_the_messages_tab_returns_the_line(self, app: App) -> None:
        # Enter on the tail resumes: the land EXECUTES inside the screen
        # and the landing line rides back for the caller to echo — the
        # dossier lands exactly the way the story browser does.
        story_id = remembered(app)
        assert browse(app, story_id, SHIFT_TAB + ENTER)


class TestEditAddressing:
    def test_an_edit_names_the_story_its_row_belongs_to(self, app: App) -> None:
        # The story in the address is CHECKED against the row, the way a
        # message edit checks its chain: a correction aimed through the
        # wrong story must not rewrite memory nobody was looking at.
        story_id = remembered(app)
        ids = app.store.stories.get_messages_ids(story_id)
        scene = app.store.scenes.get_current(story_id, ids)[0]
        app.play("/new")
        app.play("Another story begins.")
        other = app.session.story_id
        with pytest.raises(Refused):
            api_lore.edit(app.session, "scene-summary", scene.id, "hijacked", other)
        journal = app.store.journals.list(story_id)[-1]
        with pytest.raises(Refused):
            api_lore.edit(app.session, "entry", journal.id, "hijacked", other)
        assert app.store.scenes.get_current(story_id, ids)[0].summary == scene.summary
        assert app.store.journals.list(story_id)[-1].entry == journal.entry


class TestExtract:
    def test_extract_closes_a_scene_with_journals_and_rollups(self, app: App, capsys) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        out = capsys.readouterr().out
        assert "closed" in out

        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        scenes = app.store.scenes.get_current(story_id, ids)
        assert len(scenes) == 1
        assert scenes[0].title == "The Meeting"
        assert scenes[0].summary == "A guest came in and met the Keeper."
        # Both rollups landed right after the close — verbatim: a rollup
        # of one summary (or one entry) is its source, no model pass.
        assert scenes[0].history == "A guest came in and met the Keeper."
        cast = app.store.characters.list(story_id)
        assert [c.name for c in cast] == ["Keeper"]
        memory = app.store.journals.get_current(story_id, ids)[cast[0].id]
        assert memory.state == "at the gate"
        assert memory.history == "I saw the guest."
        assert len(lore_calls(app)) == 1  # the extraction; no rollup calls

    def test_an_edited_template_with_literal_json_still_extracts(self, server, tmp_path) -> None:
        # The prompts file's promise: what you edit is exactly what the
        # model sees. A template holding a literal JSON example — braces
        # and all — must render verbatim and never break the pass.
        root = tmp_path / "state"
        paths = Paths.resolve(root)
        paths.ensure_tree()
        template = (
            "You are a story analyst. Cast: {cast}\nJournals: {journals}\n"
            "Messages:\n{chunk}\n"
            'Reply as {"scene": {"title": "...", "summary": "..."}} — {this} stays literal.'
        )
        paths.prompts_file.write_text(f"extract_prompt = {json.dumps(template)}\n")
        app = launch(root, server)
        try:
            for i in range(3):
                app.play(f"Turn number {i}.")
            app.play("/extract")
            story_id = app.session.story_id
            ids = app.store.stories.get_messages_ids(story_id)
            assert len(app.store.scenes.get_current(story_id, ids)) == 1
            analyst = next(
                str(r["messages"][-1]["content"])
                for r in app.server.requests
                if "You are a story analyst" in str(r["messages"][-1]["content"])
            )
            assert '{"scene": {"title"' in analyst  # the JSON example, verbatim
            assert "{this} stays literal" in analyst
        finally:
            app.close()


class TestIdleScheduling:
    def test_a_model_turn_schedules_a_pass_that_runs_on_idle(self, server, tmp_path) -> None:
        # Thresholds shrunk so three turns are already a scene's worth, and
        # a short idle so the pass fires within the test. Playing arms the
        # pass itself — each turn goes through the REPL's own submit.
        set_config(
            tmp_path / "state",
            settle_messages=0,
            scene_min_chars=10,
            scene_min_messages=2,
            idle_seconds=0.2,
        )
        app = launch(tmp_path / "state", server)
        try:
            for i in range(3):
                app.play(f"Turn number {i}.")
            deadline = time.time() + 10
            closed = False
            while time.time() < deadline and not closed:
                ids = app.store.stories.get_messages_ids(app.session.story_id)
                closed = bool(app.store.scenes.get_current_ends(app.session.story_id, ids))
                time.sleep(0.1)
            assert closed, "the idle pass never closed a scene"
        finally:
            app.close()

    def test_the_settle_margin_holds_the_newest_messages_out(self, server, tmp_path) -> None:
        # The live rule for the AUTOMATIC pass: no scene ends where the
        # story is still moving. With a settle margin of 2, the idle pass
        # over six messages may close a scene only up to the fourth —
        # /extract is the one thing that drops this margin.
        set_config(
            tmp_path / "state",
            settle_messages=2,
            scene_min_chars=10,
            scene_min_messages=2,
            idle_seconds=0.2,
        )
        app = launch(tmp_path / "state", server)
        try:
            for i in range(3):
                app.play(f"Turn number {i}.")
            deadline = time.time() + 10
            ends: list[int] = []
            while time.time() < deadline and not ends:
                ids = app.store.stories.get_messages_ids(app.session.story_id)
                ends = app.store.scenes.get_current_ends(app.session.story_id, ids)
                time.sleep(0.1)
            assert ends, "the idle pass never closed a scene"
            assert ids.index(ends[-1]) == 3  # the newest two messages stayed open
        finally:
            app.close()


class TestRewind:
    def test_a_rewound_scene_takes_its_memory_with_it(self, app: App) -> None:
        # Undo past a scene's end: the scene is no longer current, and
        # neither is its journal — the memory follows the chain, exactly
        # as scenes do.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/undo")
        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        assert app.store.journals.get_current(story_id, ids) == {}

        # The next close starts from nothing: the abandoned timeline's
        # memories never feed the continuation.
        app.play("A different turn.")
        app.play("/extract")
        analyst = [
            str(r["messages"][-1]["content"])
            for r in app.server.requests
            if "You are a story analyst" in str(r["messages"][-1]["content"])
        ][-1]
        assert "at the gate" not in analyst  # the rewound state is gone
        assert "(none yet)" in analyst
        # The store-side of the same story: the second close WRITES its
        # scene — starting where the abandoned one did, which the
        # branching model allows — instead of dying on a uniqueness
        # constraint and killing every later pass.
        ids = app.store.stories.get_messages_ids(story_id)
        current = app.store.scenes.get_current(story_id, ids)
        assert [s.start_message_id for s in current] == [ids[0]]

    def test_a_truncated_story_extracts_again(self, app: App) -> None:
        # Resuming a story from an earlier message rewinds the head the
        # way a deep undo does (`stories.set_head` is what the browser's
        # truncate settles into) — the easiest road past a closed scene's
        # end, and the next close must land, not collide.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        # What the browser's truncate settles into, through its one door.
        from otaku.backend.api import stories as api_stories

        api_stories.land(app.session, story_id, ids[1], "truncate")
        app.play("Onward from the cut.")
        app.play("/extract")
        ids = app.store.stories.get_messages_ids(story_id)
        current = app.store.scenes.get_current(story_id, ids)
        assert [s.start_message_id for s in current] == [ids[0]]


class TestDisabled:
    def test_disabled_extraction_gates_only_the_idle_scheduling(self, server, tmp_path) -> None:
        # [lore_extraction].enabled = false stops the automatic passes and
        # nothing else — /extract keeps its one path into a pass.
        set_config(
            tmp_path / "state",
            lore_enabled=False,
            idle_seconds=0.1,
            settle_messages=0,
            scene_min_chars=10,
            scene_min_messages=2,
        )
        app = launch(tmp_path / "state", server)
        try:
            for i in range(3):
                app.play(f"Turn number {i}.")
            time.sleep(0.6)  # several idle windows — nothing may fire
            story_id = app.session.story_id
            ids = app.store.stories.get_messages_ids(story_id)
            assert app.store.scenes.get_current(story_id, ids) == []
            app.play("/extract")
            assert app.store.scenes.get_current(story_id, ids) != []
        finally:
            app.close()


class TestFailedPass:
    def test_a_garbage_reply_fails_the_pass_and_a_retry_heals(self, app: App, capsys) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.server.script = lambda body: "I cannot do JSON, sorry."
        capsys.readouterr()
        app.play("/extract")
        assert "failed" in capsys.readouterr().out
        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        assert app.store.scenes.get_current(story_id, ids) == []  # nothing half-applied

        # The story is not stuck: the next pass closes the scene normally.
        app.server.script = scripted.default_script
        app.play("/extract")
        assert "closed" in capsys.readouterr().out
        assert len(app.store.scenes.get_current(story_id, ids)) == 1

    def test_ctrl_c_cancels_the_pass_instead_of_backgrounding_it(self, app: App) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")

        def interrupting(body: dict[str, Any]) -> str:
            # The user hits Ctrl+C while the pass is at the model — the
            # model stays busy long enough for the press to land mid-wait,
            # and the reply is valid, so only a real cancel keeps the
            # scene out.
            signal.raise_signal(signal.SIGINT)
            time.sleep(0.3)
            return scripted.default_script(body)

        app.server.script = interrupting
        with contextlib.suppress(KeyboardInterrupt):
            app.play("/extract")
        time.sleep(0.3)  # a backgrounded pass would commit in this window
        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        assert app.store.scenes.get_current(story_id, ids) == []

        app.server.script = scripted.default_script
        app.play("/extract")
        assert len(app.store.scenes.get_current(story_id, ids)) == 1

    def test_an_empty_reply_is_a_failure_not_a_cancellation(self, app: App, capsys) -> None:
        # Nobody cancelled anything: an empty reply must land on the loud
        # FAILED path, or a model that answers nothing builds no memory,
        # silently forever.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.server.script = lambda body: ""
        capsys.readouterr()
        app.play("/extract")
        out = capsys.readouterr().out
        assert "failed" in out
        assert "ancelled" not in out
        logs = app.paths.root / "logs"
        assert any("the reply was empty" in f.read_text() for f in logs.rglob("*") if f.is_file())

    def test_a_scene_without_a_summary_is_refused(self, app: App, capsys) -> None:
        # A committed scene whose summary is empty would swallow its span:
        # not in the head, not in the tail, not in the recap. Refused whole.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.server.script = lambda body: json.dumps(
            {"scene": {"title": "Untold"}, "speakers": [], "characters": [], "journals": []}
        )
        capsys.readouterr()
        app.play("/extract")
        assert "failed" in capsys.readouterr().out
        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        assert app.store.scenes.get_current(story_id, ids) == []
        assert app.store.characters.list(story_id) == []  # nothing half-applied

        app.server.script = scripted.default_script
        app.play("/extract")
        assert len(app.store.scenes.get_current(story_id, ids)) == 1

    def test_an_unexpected_crash_is_logged_not_swallowed(
        self, app: App, capsys, monkeypatch
    ) -> None:
        class Exploding:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def run(self, **kwargs: Any) -> None:
                raise RuntimeError("boom")

        monkeypatch.setattr("otaku.worker.Extractor", Exploding)
        app.play("I enter the hall.")
        capsys.readouterr()
        app.play("/extract")
        assert "failed" in capsys.readouterr().out
        logs = list((app.paths.root / "logs").rglob("*"))
        assert any("RuntimeError" in f.read_text() for f in logs if f.is_file())
        # The error log holds the WHOLE traceback, not just the shape.
        errors = [f for f in logs if f.name.startswith("error-")]
        assert errors and "Traceback" in errors[0].read_text()
        assert "boom" in errors[0].read_text()


class TestHealing:
    def test_an_import_with_a_hole_in_memory_heals_exactly_the_hole(
        self, app: App, tmp_path
    ) -> None:
        # The chapel export, with one character's history blanked: the
        # next pass rebuilds exactly the missing rollup — from the one
        # entry, verbatim — and touches nothing that is already there.
        export = imports.parse_story(CHAPEL.read_text())
        scene = export.scenes[-1]
        journals = tuple(
            replace(j, history="") if j.character == "Кассиан" else j for j in scene.journals
        )
        holed = replace(export, scenes=(*export.scenes[:-1], replace(scene, journals=journals)))
        path = tmp_path / "chapel-holed.md"
        path.write_text(
            exports.render_story(
                holed, otaku_version="0", model="test/test-model", exported="2026-07-30 12:00"
            )
        )

        app.play(f"/import {path}")
        assert app.server.requests == []  # a native import triggers nothing

        app.play("/extract")  # the next pass heals the hole...
        assert app.server.requests == []  # ...verbatim: one entry IS the history
        story_id = app.session.story_id
        cast = {c.name: c.id for c in app.store.characters.list(story_id)}
        ids = app.store.stories.get_messages_ids(story_id)
        memories = app.store.journals.get_current(story_id, ids)
        holed = next(j for j in journals if j.character == "Кассиан")
        assert memories[cast["Кассиан"]].history == holed.entry
        # The other character's memory came through untouched.
        untouched = next(j for j in scene.journals if j.character == "Элоиза")
        assert memories[cast["Элоиза"]].history == untouched.history

    def test_an_edited_summary_heals_every_arc_after_it(self, server, tmp_path) -> None:
        # Editing an early summary nulls the arcs composed from it — that
        # scene's and every later one's — and the next pass rebuilds them
        # ALL, each from the summaries up to its own scene: the arc
        # exists on every scene again, not only the newest.
        set_config(
            tmp_path / "state",
            settle_messages=0,
            scene_min_chars=40,
            scene_min_messages=4,
        )
        app = launch(tmp_path / "state", server)
        server.script = numbered_script()
        try:
            played_chapters(app, 6)  # 12 messages → three 4-message scenes
            app.play("/extract")
            story_id = app.session.story_id
            ids = app.store.stories.get_messages_ids(story_id)
            scenes = app.store.scenes.get_current(story_id, ids)
            assert all(s.history for s in scenes)  # written as each closed

            api_lore.edit(
                app.session, "scene-summary", scenes[0].id, "A corrected opening.", story_id
            )
            scenes = app.store.scenes.get_current(story_id, ids)
            assert not any(s.history for s in scenes)  # composed from the old text

            calls_before = len(lore_calls(app))
            app.play("/extract")  # declines a new scene, heals every hole
            scenes = app.store.scenes.get_current(story_id, ids)
            assert scenes[0].history == "A corrected opening."  # one summary, verbatim
            assert scenes[1].history == scripted.STORY_SO_FAR
            assert scenes[2].history == scripted.STORY_SO_FAR
            # One rollup call per healed multi-summary scene, nothing more.
            assert len(lore_calls(app)) == calls_before + 2
        finally:
            app.close()


class TestLongStory:
    """A story long enough for several scenes: the backlog packs into
    same-sized scenes, and each character's memory threads through the
    spans."""

    def test_a_long_backlog_closes_into_several_scenes(self, server, tmp_path) -> None:
        set_config(
            tmp_path / "state",
            settle_messages=0,
            scene_min_chars=40,
            scene_min_messages=4,
        )
        app = launch(tmp_path / "state", server)
        server.script = numbered_script()
        try:
            played_chapters(app, 6)  # 12 messages → three 4-message scenes
            app.play("/extract")

            story_id = app.session.story_id
            ids = app.store.stories.get_messages_ids(story_id)
            scenes = app.store.scenes.get_current(story_id, ids)
            assert [s.title for s in scenes] == ["Scene 1", "Scene 2", "Scene 3"]
            # The spans tile the story: contiguous, in order, nothing shared.
            bounds = [(ids.index(s.start_message_id), ids.index(s.end_message_id)) for s in scenes]
            assert bounds == [(0, 3), (4, 7), (8, 11)]
            # One journal entry per scene; the newest state stands.
            keeper = app.store.characters.list(story_id)[0]
            memory = app.store.journals.get_current(story_id, ids)[keeper.id]
            assert memory.state == "state 3"
            # Every scene carries its own arc, written as it closed: the
            # first is its one summary verbatim, the rest are rollups.
            assert scenes[0].history == "Scene summary 1."
            assert scenes[1].history == scripted.STORY_SO_FAR
            assert scenes[2].history == scripted.STORY_SO_FAR
        finally:
            app.close()

    def test_each_scene_feeds_the_next_extraction(self, server, tmp_path) -> None:
        # The feedback loop: by the time scene 2 is extracted, the prompt
        # already carries the character's rolled-up memory of scene 1 —
        # their story continues instead of restarting.
        set_config(
            tmp_path / "state",
            settle_messages=0,
            scene_min_chars=40,
            scene_min_messages=4,
        )
        app = launch(tmp_path / "state", server)
        server.script = numbered_script()
        try:
            played_chapters(app, 4)  # 8 messages → two scenes
            app.play("/extract")
            extractions = [
                str(request["messages"][-1]["content"])
                for request in app.server.requests
                if "You are a story analyst" in str(request["messages"][-1]["content"])
            ]
            assert len(extractions) == 2
            assert "(none yet)" in extractions[0]  # scene 1 starts from nothing
            assert "so far: Entry 1." in extractions[1]  # scene 1's one entry, verbatim
            assert "now: state 1" in extractions[1]
        finally:
            app.close()


class TestRecap:
    def test_the_recap_reaches_the_wire_after_a_scene_closes(self, server, tmp_path) -> None:
        # A window of head 1 + tail 1, so even a short story outgrows it
        # and the closed scene must stand in as the recap.
        set_config(tmp_path / "state", head_messages=1, min_tail_messages=1)
        app = launch(tmp_path / "state", server)
        try:
            for i in range(6):
                app.play(f"Turn number {i}.")
            app.play("/extract")
            # Two turns past the close: the tail is a MINIMUM, so the
            # scene must end strictly before its first message to be
            # summarized — one turn of clearance puts it there.
            app.play("We continue.")
            app.play("We walk on.")
            wire = scripted.chat_request(app.server, "We walk on.")
            sent = "\n".join(m["content"] for m in wire["messages"])
            assert "[The story so far — the scenes between these moments:]" in sent
            assert "A guest came in and met the Keeper." in sent
        finally:
            app.close()

    def test_the_middle_of_the_story_reaches_the_wire_as_summaries(self, server, tmp_path) -> None:
        set_config(
            tmp_path / "state",
            settle_messages=0,
            scene_min_chars=40,
            scene_min_messages=4,
            head_messages=2,
            min_tail_messages=3,
        )
        app = launch(tmp_path / "state", server)
        server.script = numbered_script()
        try:
            played_chapters(app, 6)
            app.play("/extract")
            app.play("We walk on.")

            wire = scripted.chat_request(app.server, "We walk on.")
            sent = "\n".join(m["content"] for m in wire["messages"])
            # The opening is verbatim — the style anchor.
            assert "Turn number 0." in sent
            # The middle is its summaries, in story order.
            assert "[The story so far — the scenes between these moments:]" in sent
            assert sent.index("Scene summary 1.") < sent.index("Scene summary 2.")
            # A scene ending inside the tail window stays verbatim instead.
            assert "Scene summary 3." not in sent
            assert "Turn number 4." in sent and "Turn number 5." in sent
            # And the summarized middle's own messages are gone from the wire.
            assert "Turn number 1." not in sent
            assert "Turn number 2." not in sent
        finally:
            app.close()

    def test_an_overgrown_recap_leads_with_the_story_so_far(self, server, tmp_path) -> None:
        # Summaries too big for the recap's budget share: the oldest fall
        # out and the story-so-far THROUGH the last dropped scene stands
        # in for them — the story never outgrows its own recap, and the
        # summaries still riding verbatim are not retold inside it.
        set_config(
            tmp_path / "state",
            settle_messages=0,
            scene_min_chars=40,
            scene_min_messages=4,
            head_messages=2,
            min_tail_messages=3,
            max_context=3000,
        )
        app = launch(tmp_path / "state", server)
        server.script = numbered_script(summary_chars=4000)
        try:
            played_chapters(app, 8)
            app.play("/extract")
            app.play("We walk on.")

            wire = scripted.chat_request(app.server, "We walk on.")
            sent = "\n".join(m["content"] for m in wire["messages"])
            assert scripted.STORY_SO_FAR in sent  # the last dropped scene's rollup
            assert "Scene summary 3." in sent  # the newest covered summary stays
            assert "Scene summary 1." not in sent  # covered by the rollup instead
            assert "Scene summary 2." not in sent  # covered by the rollup instead
        finally:
            app.close()

    def test_over_the_limit_without_scenes_the_turn_is_declined(
        self, server, tmp_path, capsys
    ) -> None:
        # No scene has closed and the story outgrows the limit: nothing
        # can degrade, so the turn records and the reply is declined with
        # the sentence naming the remedies — never a silently dropped
        # middle.
        set_config(tmp_path / "state", head_messages=2)
        app = launch(tmp_path / "state", server)
        try:
            for i in range(8):
                app.play(f"Turn number {i}. " + "x" * 8000)
            before = len(app.server.requests)
            capsys.readouterr()
            app.play("We continue.")
            assert len(app.server.requests) == before  # nothing was sent
            assert "does not fit the context limit" in capsys.readouterr().out
            chain = app.store.stories.get_messages(app.session.story_id)
            assert chain[-1].body == "We continue."  # the turn is story and stands
        finally:
            app.close()


class TestMerge:
    def test_merge_folds_a_duplicate_into_the_real_character(self, app: App, capsys) -> None:
        # Two passes, each with its own spelling of the same character:
        # the duplicate folds in, its journal follows, the name becomes
        # an alias.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # brings the Keeper

        def duplicated(body: dict[str, Any]) -> str:
            prompt = str(body.get("messages", [{}])[-1].get("content", ""))
            if "You are a story analyst" not in prompt:
                return scripted.default_script(body)
            return json.dumps(
                {
                    "scene": {"title": "The Return", "summary": "The guest returned."},
                    "speakers": [],
                    "characters": [{"name": "The Keeper", "description": "the same warden"}],
                    "journals": [
                        {"character": "The Keeper", "entry": "Back again.", "state": "by the door"}
                    ],
                }
            )

        app.server.script = duplicated
        for i in range(3):
            app.play(f"Turn number {3 + i}.")
        app.play("/extract")  # brings the duplicate
        story_id = app.session.story_id
        assert [c.name for c in app.store.characters.list(story_id)] == ["Keeper", "The Keeper"]

        app.play("/merge The Keeper into Keeper")
        cast = app.store.characters.list(story_id)
        assert [c.name for c in cast] == ["Keeper"]
        assert "The Keeper" in cast[0].aliases
        # The duplicate's newer journal followed the merge.
        ids = app.store.stories.get_messages_ids(story_id)
        assert app.store.journals.get_current(story_id, ids)[cast[0].id].state == "by the door"

    def test_merge_refuses_to_destroy_an_imported_card(self, app: App) -> None:
        # A merge deletes the source row, archive and all — so a card
        # carrier may only be merged INTO, never away.
        app.play(f"/card {SERAPHINA}")
        story_id = app.session.story_id
        app.store.characters.add(story_id, "Sera the Second")
        app.play("/merge Seraphina into Sera the Second")
        kept = app.store.characters.find(story_id, "Seraphina")
        assert kept is not None and kept.card  # refused: the archive survives
        assert app.store.characters.find(story_id, "Sera the Second").card is None

        app.play("/merge Sera the Second into Seraphina")
        merged = app.store.characters.find(story_id, "Sera the Second")
        assert merged is not None and merged.id == kept.id  # now an alias
        assert merged.card == kept.card

    def test_merge_invalidates_the_rolled_up_memory(self, app: App) -> None:
        # A rollup composed before the merge covers only one side; the
        # merge resets the histories and the next pass rebuilds the one
        # memory from the union of entries.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")

        def duplicated(body: dict[str, Any]) -> str:
            prompt = str(body.get("messages", [{}])[-1].get("content", ""))
            if "You are a story analyst" not in prompt:
                return scripted.default_script(body)
            return json.dumps(
                {
                    "scene": {"title": "The Return", "summary": "The guest returned."},
                    "speakers": [],
                    "characters": [{"name": "The Keeper", "description": "the same warden"}],
                    "journals": [
                        {"character": "The Keeper", "entry": "Back again.", "state": "by the door"}
                    ],
                }
            )

        app.server.script = duplicated
        for i in range(3):
            app.play(f"Turn number {3 + i}.")
        app.play("/extract")
        app.play("/merge The Keeper into Keeper")
        story_id = app.session.story_id
        keeper = app.store.characters.list(story_id)[0]
        ids = app.store.stories.get_messages_ids(story_id)
        assert not app.store.journals.get_current(story_id, ids)[keeper.id].history

        app.server.script = scripted.default_script
        app.play("/extract")  # declines a scene, rebuilds the memory
        assert app.store.journals.get_current(story_id, ids)[keeper.id].history
        rebuild = next(
            str(r["messages"][-1]["content"])
            for r in reversed(app.server.requests)
            if str(r["messages"][-1]["content"]).startswith("Write ")
        )
        assert "I saw the guest." in rebuild  # the union of entries...
        assert "Back again." in rebuild  # ...feeds the one memory


class TestWarmUp:
    """The post-close prompt warm-up exists for a LOCAL server's prefix
    cache. A cloud provider has no per-session cache the request could
    warm — sending it there bills a full context window for one token —
    so the close warms local providers and never a hosted catalog."""

    def test_a_local_close_warms_the_next_prompt(
        self, server: scripted.ModelServer, tmp_path: Path
    ) -> None:
        # The same scripted server behind a provider the registry builds
        # as a provider ON THIS MACHINE — the section's NAME picks the
        # class.
        root = tmp_path / "state"
        set_config_provider(root, server, name="llamacpp")
        app = launch(root, server, spec="llamacpp/test-model")
        try:
            remembered(app)
            # The waiter is answered BEFORE the warm-up, so the request
            # may still be in flight when /extract returns.
            assert _warm_requests(app.server, within=5.0) == 1
        finally:
            app.close()

    def test_the_generic_provider_never_warms(
        self, server: scripted.ModelServer, tmp_path: Path
    ) -> None:
        # Its url could name a hosted catalog and the app cannot tell —
        # so the one request that could bill a window for a token is
        # never sent there.
        root = tmp_path / "state"
        set_config_provider(root, server, name="generic")
        app = launch(root, server, spec="generic/test-model")
        try:
            remembered(app)
            time.sleep(1.5)  # the window the local warm-up starts within
            assert _warm_requests(app.server, within=0) == 0
        finally:
            app.close()

    def test_a_cloud_close_never_warms(self, server: scripted.ModelServer, tmp_path: Path) -> None:
        # The same scripted server behind a provider the registry builds
        # as a CLOUD client — the section's NAME picks the class.
        root = tmp_path / "state"
        set_config_provider(root, server, name="openrouter")
        app = launch(root, server, spec="openrouter/test-model")
        try:
            remembered(app)
            time.sleep(1.5)  # the window the local warm-up starts within
            assert _warm_requests(app.server, within=0) == 0
        finally:
            app.close()


def _warm_requests(server: scripted.ModelServer, *, within: float) -> int:
    """How many warm-up requests the server has seen — the one-token
    prefill is the only request the app ever caps at a single token.
    Polls up to `within` seconds, because the warm-up runs on the
    worker's thread after the pass's waiter is answered."""
    deadline = time.monotonic() + within
    while True:
        seen = sum(1 for r in server.requests if r.get("max_tokens") == 1)
        if seen or time.monotonic() >= deadline:
            return seen
        time.sleep(0.05)


def remembered(app: App) -> int:
    """A story with memory: one closed scene, the Keeper, a journal."""
    for i in range(3):
        app.play(f"Turn number {i}.")
    app.play("/extract")
    return app.session.story_id


def browse(app: App, story_id: int, keys: str) -> str | None:
    """Drive the dossier over the open story, opened on scenes the way
    /lore opens it; a landing's line comes back, as it does to /lore."""
    with contextlib.suppress(EOFError):
        return run_screen(keys, lambda: screen_story.browse(app.session, "scenes"))
    return None


def lore_calls(app: App) -> list[str]:
    """The recorded lore-building prompts — the post-close prompt warm-up
    is the app's own background business and doesn't count."""
    prompts = [str(r["messages"][-1]["content"]) for r in app.server.requests]
    return [
        p for p in prompts if "You are a story analyst" in p or p.startswith(("Combine", "Write "))
    ]


def played_chapters(app: App, turns: int) -> None:
    """`turns` short exchanges — a backlog worth several scenes under the
    shrunk thresholds."""
    for i in range(turns):
        app.play(f"Turn number {i}.")
