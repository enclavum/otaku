"""Meta commands: /help and /bye — about the app, not the story."""

from scenarios.support.harness import App


class TestBye:
    def test_bye_asks_the_loop_to_quit(self, app: App) -> None:
        app.play("/bye")
        assert app.session.should_quit is True


class TestHelp:
    def test_help_lists_the_commands(self, app: App, capsys) -> None:
        app.play("/help")
        out = capsys.readouterr().out
        for command in ("/model", "/stories", "/extract", "/undo", "/bye"):
            assert command in out


class TestUnknown:
    def test_an_unknown_command_is_refused_not_played(self, app: App, capsys) -> None:
        app.play("/abracadabra")
        assert "abracadabra" in capsys.readouterr().out
        assert app.session.story_id is None  # nothing was created...
        assert app.server.requests == []  # ...and nothing was sent
