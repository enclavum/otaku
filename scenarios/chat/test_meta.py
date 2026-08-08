"""Meta commands: /help and /bye — about the app, not the story."""

from scenarios.support.harness import App


class TestCrashContainment:
    def test_a_crashing_command_never_kills_the_session(
        self, app: App, capsys, monkeypatch
    ) -> None:
        def boom(session: object, store: object, args: object) -> None:
            raise RuntimeError("boom")

        from otaku.chat import commands

        monkeypatch.setitem(commands.COMMANDS, "/help", (boom, None))
        app.play("/help")
        out = capsys.readouterr().out
        assert "command failed (RuntimeError)" in out
        assert "error-" in out  # the notice names the log
        errors = [f for f in (app.paths.root / "logs").rglob("error-*") if f.is_file()]
        assert errors and "Traceback" in errors[0].read_text()
        # The session lives on: the next line plays normally.
        app.play("I enter the hall.")
        assert app.session.messages[-1].body


class TestUnknown:
    def test_an_unknown_command_is_refused_not_played(self, app: App, capsys) -> None:
        app.play("/abracadabra")
        assert "abracadabra" in capsys.readouterr().out
        assert app.session.story_id is None  # nothing was created...
        assert app.server.requests == []  # ...and nothing was sent
