"""The lore engine: /extract builds the memory, and the memory is used."""

import time

from otaku.chat.commands.lore import build_job
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, write_config


class TestExtract:
    def test_extract_closes_a_scene_with_journals_and_rollups(self, app: App, capsys) -> None:
        for i in range(3):
            app.play(f"Ход номер {i}.")
        app.play("/extract")
        out = capsys.readouterr().out
        assert "closed" in out

        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        scenes = app.store.scenes.get_current(story_id, ids)
        assert len(scenes) == 1
        assert scenes[0].title == "Встреча"
        assert scenes[0].summary == "Гость вошёл и встретил Хранителя."
        # Both rollups landed right after the close.
        assert scenes[0].history == scripted.STORY_SO_FAR
        cast = app.store.characters.list(story_id)
        assert [c.name for c in cast] == ["Хранитель"]
        memory = app.store.journals.get_current(story_id)[cast[0].id]
        assert memory.state == "у врат"
        assert memory.history == scripted.CHARACTER_HISTORY

    def test_extract_with_nothing_new_declines(self, app: App, capsys) -> None:
        app.play("Один ход.")
        app.play("/extract")
        capsys.readouterr()
        app.play("/extract")
        assert "Nothing new since the last scene." in capsys.readouterr().out

    def test_the_recap_reaches_the_wire_after_a_scene_closes(self, server, tmp_path) -> None:
        # A window of head 1 + tail 1, so even a short story outgrows it
        # and the closed scene must stand in as the recap.
        write_config(tmp_path / "state", server, head_messages=1, tail_messages=1)
        app = launch(tmp_path / "state", server)
        try:
            for i in range(6):
                app.play(f"Ход номер {i}.")
            app.play("/extract")
            app.play("Продолжаем.")
            sent = "\n".join(m["content"] for m in app.server.requests[-1]["messages"])
            assert "[The story so far — the scenes between these moments:]" in sent
            assert "Гость вошёл и встретил Хранителя." in sent
        finally:
            app.close()


class TestIdleScheduling:
    def test_a_model_turn_schedules_a_pass_that_runs_on_idle(self, server, tmp_path) -> None:
        # Thresholds shrunk so three turns are already a scene's worth, and
        # a short idle so the pass fires within the test.
        write_config(
            tmp_path / "state",
            server,
            settle_messages=0,
            scene_min_chars=10,
            scene_min_messages=2,
        )
        app = launch(tmp_path / "state", server, idle_seconds=0.2)
        try:
            for i in range(3):
                app.play(f"Ход номер {i}.")
            # What the REPL does after a model turn lands:
            app.worker.schedule(build_job(app.session))
            deadline = time.time() + 10
            closed = False
            while time.time() < deadline and not closed:
                ids = app.store.stories.get_messages_ids(app.session.story_id)
                closed = bool(app.store.scenes.get_current_ends(app.session.story_id, ids))
                time.sleep(0.1)
            assert closed, "the idle pass never closed a scene"
        finally:
            app.close()
