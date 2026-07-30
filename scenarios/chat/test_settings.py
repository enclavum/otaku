"""The model and the knobs: /model, /set think, /set verbose,
/set parameter — and what each remembers across a relaunch.

The design: `/set think` and `/set verbose` are session-wide and persist
in the app's own state; `/set parameter` follows the MODEL it was set on;
a model switch keeps the story context and is remembered as last used.
"""

from otaku.chat.session import TUI
from otaku.tui import models
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config_provider
from scenarios.support.screens import ENTER, ESC, run_screen
from scenarios.support.server import ModelServer


class TestModel:
    def test_a_direct_switch_keeps_the_context_and_changes_the_wire(
        self, app: App, capsys
    ) -> None:
        app.play("I enter the hall.")
        app.play("/model test/other-model")
        app.play("I look around.")
        assert app.server.requests[-1]["model"] == "other-model"
        # The story context traveled with the switch.
        assert [m.body for m in app.session.messages][:2] == [
            "I enter the hall.",
            scripted.CHAT_REPLY,
        ]

    def test_the_switch_is_remembered_as_last_used(self, app: App) -> None:
        app.play("/model test/other-model")
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.model == "other-model"
        relaunched.close()

    def test_switching_to_the_same_model_says_so(self, app: App, capsys) -> None:
        app.play("/model test/test-model")
        assert "Already using test/test-model." in capsys.readouterr().out

    def test_an_unknown_provider_is_refused_with_the_known_ones(
        self, app: App, capsys
    ) -> None:
        app.play("/model nowhere/some-model")
        out = capsys.readouterr().out
        assert "test" in out  # the configured providers are listed
        assert app.session.model == "test-model"

    def test_bare_model_opens_the_picker_on_the_current_model(self, app: App) -> None:
        opened_on: list[str] = []

        def pick(current: str) -> str:
            opened_on.append(current)
            return "test/other-model"

        app.session.tui = TUI(pick_model=pick)
        app.play("/model")
        assert opened_on == ["test/test-model"]
        assert app.session.model == "other-model"

    def test_cancelling_the_picker_keeps_the_model(self, app: App) -> None:
        app.session.tui = TUI(pick_model=lambda current: None)
        app.play("/model")
        assert app.session.model == "test-model"

class TestModelPicker:
    def test_enter_picks_the_highlighted_model(self, app: App) -> None:
        spec = run_screen(ENTER, lambda: models.pick(app.session.providers))
        assert spec == "test/test-model"

    def test_esc_cancels_without_picking(self, app: App) -> None:
        assert run_screen(ESC, lambda: models.pick(app.session.providers)) is None

    def test_the_cursor_restores_to_the_last_used_model(self, tmp_path) -> None:
        server = ModelServer(models=("alpha", "beta"))
        try:
            app = launch(tmp_path / "state", server)
            try:
                registry = app.session.providers
                first = run_screen(ENTER, lambda: models.pick(registry))
                assert first == "test/alpha"
                resumed = run_screen(
                    ENTER, lambda: models.pick(registry, initial_spec="test/beta")
                )
                assert resumed == "test/beta"
            finally:
                app.close()
        finally:
            server.close()

    def test_the_filter_narrows_the_list(self, tmp_path) -> None:
        server = ModelServer(models=("alpha", "beta"))
        try:
            app = launch(tmp_path / "state", server)
            try:
                registry = app.session.providers
                spec = run_screen(f"/bet{ENTER}{ENTER}", lambda: models.pick(registry))
                assert spec == "test/beta"
            finally:
                app.close()
        finally:
            server.close()

class TestThink:
    def test_a_level_is_set_and_remembered(self, app: App, capsys) -> None:
        app.play("/set think high")
        assert "high" in capsys.readouterr().out
        assert app.session.think == "high"
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.think == "high"
        relaunched.close()

    def test_on_and_off_are_aliases(self, app: App) -> None:
        app.play("/set think on")
        assert app.session.think == "medium"
        app.play("/set think off")
        assert app.session.think == "none"

    def test_default_means_the_model_decides(self, app: App) -> None:
        app.play("/set think default")
        assert app.session.think is None
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.think is None
        relaunched.close()

    def test_bare_shows_the_current_level(self, app: App, capsys) -> None:
        app.play("/set think low")
        capsys.readouterr()
        app.play("/set think")
        assert "low" in capsys.readouterr().out

    def test_an_unknown_level_shows_the_usage(self, app: App, capsys) -> None:
        app.play("/set think enormous")
        assert "Usage" in capsys.readouterr().out
        assert app.session.think == "none"  # unchanged from the default

    def test_the_level_rides_the_wire_and_default_sends_nothing(self, app: App) -> None:
        app.play("/set think low")
        app.play("I enter the hall.")
        assert app.server.requests[-1]["reasoning_effort"] == "low"
        app.play("/set think default")
        app.play("I look around.")
        assert "reasoning_effort" not in app.server.requests[-1]

    def test_thinking_streams_but_is_never_saved(self, app: App, capsys) -> None:
        app.play("/set think high")
        app.server.script = lambda body: ("Let me consider the hall.", "The door creaks open.")
        app.play("I enter the hall.")
        assert "(thinking) Let me consider the hall." in capsys.readouterr().out
        # Only the reply became part of the story...
        assert [m.body for m in app.session.messages] == [
            "I enter the hall.",
            "The door creaks open.",
        ]
        # ...so the next turn's context carries no thinking either.
        app.play("I look around.")
        assert "consider" not in str(app.server.requests[-1]["messages"])

    def test_a_provider_without_thinking_refuses_the_knob(
        self, server, tmp_path, capsys
    ) -> None:
        set_config_provider(tmp_path / "state", server, supports_thinking=False)
        plain = launch(tmp_path / "state", server)
        try:
            plain.play("/set think high")
            assert "not supported" in capsys.readouterr().out
            assert plain.session.think != "high"
        finally:
            plain.close()

class TestParameters:
    def test_a_set_parameter_reaches_the_wire(self, app: App) -> None:
        app.play("/set parameter temperature 0.7")
        app.play("I enter the hall.")
        assert app.server.requests[-1]["temperature"] == 0.7

    def test_the_parameter_is_remembered_for_the_model(self, app: App) -> None:
        app.play("/set parameter temperature 0.7")
        relaunched = launch(app.paths.root, app.server)
        relaunched.play("I enter the hall.")
        assert relaunched.server.requests[-1]["temperature"] == 0.7
        relaunched.close()

    def test_reset_returns_the_parameter_to_the_default(self, app: App) -> None:
        app.play("/set parameter temperature 0.7")
        app.play("/set parameter temperature reset")
        app.play("I enter the hall.")
        assert "temperature" not in app.server.requests[-1]

    def test_parameters_follow_their_model_across_a_switch(self, app: App) -> None:
        # Set on one model, switch away: the other model plays with ITS
        # saved parameters, not the first one's.
        app.play("/set parameter temperature 0.7")
        app.play("/model test/other-model")
        app.play("/set parameter top_p 0.5")
        app.play("I enter the hall.")
        body = app.server.requests[-1]
        assert body["top_p"] == 0.5
        assert "temperature" not in body
        # And switching back restores the first model's own parameters.
        app.play("/model test/test-model")
        app.play("I look around.")
        body = app.server.requests[-1]
        assert body["temperature"] == 0.7
        assert "top_p" not in body

    def test_an_invalid_value_is_refused(self, app: App, capsys) -> None:
        app.play("/set parameter temperature warm")
        assert "warm" in capsys.readouterr().out
        app.play("I enter the hall.")
        assert "temperature" not in app.server.requests[-1]

    def test_an_unknown_parameter_is_refused(self, app: App, capsys) -> None:
        app.play("/set parameter charisma 18")
        assert "charisma" in capsys.readouterr().out
        app.play("I enter the hall.")
        assert "charisma" not in app.server.requests[-1]

    def test_bare_set_shows_the_usage(self, app: App, capsys) -> None:
        app.play("/set")
        assert "Usage" in capsys.readouterr().out

class TestVerbose:
    def test_verbose_adds_the_stats_line_after_a_reply(self, app: App, capsys) -> None:
        app.play("/set verbose on")
        app.play("I enter the hall.")
        assert "[ total" in capsys.readouterr().out

    def test_off_removes_it(self, app: App, capsys) -> None:
        app.play("/set verbose on")
        app.play("/set verbose off")
        capsys.readouterr()
        app.play("I enter the hall.")
        assert "[ total" not in capsys.readouterr().out

    def test_the_toggle_is_remembered(self, app: App) -> None:
        app.play("/set verbose on")
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.verbose is True
        relaunched.close()
