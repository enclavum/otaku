"""The lore engine: /extract builds the memory, and the memory is used."""

import time

from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config
from scenarios.support.server import numbered_script


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
        # Both rollups landed right after the close.
        assert scenes[0].history == scripted.STORY_SO_FAR
        cast = app.store.characters.list(story_id)
        assert [c.name for c in cast] == ["Keeper"]
        memory = app.store.journals.get_current(story_id)[cast[0].id]
        assert memory.state == "at the gate"
        assert memory.history == scripted.CHARACTER_HISTORY

    def test_extract_with_nothing_new_declines(self, app: App, capsys) -> None:
        app.play("A single turn.")
        app.play("/extract")
        capsys.readouterr()
        app.play("/extract")
        assert "Nothing new since the last scene." in capsys.readouterr().out

    def test_the_recap_reaches_the_wire_after_a_scene_closes(self, server, tmp_path) -> None:
        # A window of head 1 + tail 1, so even a short story outgrows it
        # and the closed scene must stand in as the recap.
        set_config(tmp_path / "state", head_messages=1, tail_messages=1)
        app = launch(tmp_path / "state", server)
        try:
            for i in range(6):
                app.play(f"Turn number {i}.")
            app.play("/extract")
            app.play("We continue.")
            sent = "\n".join(m["content"] for m in app.server.requests[-1]["messages"])
            assert "[The story so far — the scenes between these moments:]" in sent
            assert "A guest came in and met the Keeper." in sent
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


def played_chapters(app: App, turns: int) -> None:
    """`turns` short exchanges — a backlog worth several scenes under the
    shrunk thresholds."""
    for i in range(turns):
        app.play(f"Turn number {i}.")


class TestLongStory:
    """A story long enough for several scenes: the backlog packs into
    same-sized scenes, each character's memory threads through the spans,
    and the middle of the story reaches the wire as its summaries."""

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
            memory = app.store.journals.get_current(story_id)[keeper.id]
            assert memory.state == "state 3"
            assert scenes[-1].history == scripted.STORY_SO_FAR
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
            assert f"so far: {scripted.CHARACTER_HISTORY}" in extractions[1]
            assert "now: state 1" in extractions[1]
        finally:
            app.close()

    def test_the_middle_of_the_story_reaches_the_wire_as_summaries(self, server, tmp_path) -> None:
        set_config(
            tmp_path / "state",
            settle_messages=0,
            scene_min_chars=40,
            scene_min_messages=4,
            head_messages=2,
            tail_messages=3,
        )
        app = launch(tmp_path / "state", server)
        server.script = numbered_script()
        try:
            played_chapters(app, 6)
            app.play("/extract")
            app.play("We walk on.")

            sent = "\n".join(m["content"] for m in app.server.requests[-1]["messages"])
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
        # out and the story-so-far rollup stands in for them — the story
        # never outgrows its own recap.
        set_config(
            tmp_path / "state",
            settle_messages=0,
            scene_min_chars=40,
            scene_min_messages=4,
            head_messages=2,
            tail_messages=3,
        )
        app = launch(tmp_path / "state", server)
        server.script = numbered_script(summary_chars=4000)
        try:
            played_chapters(app, 6)
            app.play("/extract")
            app.play("We walk on.")

            sent = "\n".join(m["content"] for m in app.server.requests[-1]["messages"])
            assert scripted.STORY_SO_FAR in sent  # the rollup, standing in
            assert "Scene summary 2." in sent  # the newest covered summary stays
            assert "Scene summary 1." not in sent  # the oldest fell out
        finally:
            app.close()
