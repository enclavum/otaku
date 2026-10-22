"""Meta commands: /help and /quit — about the app, not the story."""

from scenarios.support.harness import App


class TestCrashContainment:
    def test_a_crashing_command_never_kills_the_session(
        self, app: App, capsys, monkeypatch
    ) -> None:
        def boom(chat: object, raw: object) -> None:
            raise RuntimeError("boom")

        from otaku.terminal import dispatch

        monkeypatch.setitem(dispatch.HANDLERS, "/help", boom)
        app.play("/help")
        out = capsys.readouterr().out
        assert "Command failed (RuntimeError)" in out
        assert "error-" in out  # the notice names the log
        errors = [f for f in (app.paths.root / "logs").rglob("error-*") if f.is_file()]
        assert errors and "Traceback" in errors[0].read_text()
        # The session lives on: the next line plays normally.
        app.play("I enter the hall.")
        assert app.messages[-1].body


class TestUnknown:
    def test_an_unknown_command_is_refused_not_played(self, app: App, capsys) -> None:
        app.play("/abracadabra")
        assert "abracadabra" in capsys.readouterr().out
        assert app.story_id is None  # nothing was created...
        assert app.server.requests == []  # ...and nothing was sent


class TestQuit:
    def test_quit_and_its_old_names_end_the_session(self, app: App) -> None:
        # `/bye`, its name before 0.9.0, and `/exit` are hidden aliases
        for typed in ("/quit", "/bye", "/exit"):
            app.chat.quit = False
            app.play(typed)
            assert app.chat.quit, typed
