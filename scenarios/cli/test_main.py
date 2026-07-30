"""End-to-end stories: the real binary in a pty, driven by keystrokes.

These are the literal user stories — slower than the in-process kind, so
only journeys that need the real REPL (keybindings, pickers on screen,
process lifecycle) live here.
"""

from pathlib import Path

from otaku.paths import Paths
from otaku.settings import state as state_mod
from scenarios.support import server as scripted
from scenarios.support.harness import SPEC, set_config_provider
from scenarios.support.process import run_otaku
from scenarios.support.server import ModelServer
from scenarios.support.terminal import CTRL_O, CTRL_R, CTRL_T, CTRL_U, ENTER, Terminal


def remember(root: Path) -> None:
    """state.toml pointing at the scripted model, so launch lands in the
    REPL instead of the picker."""
    state_mod.save(Paths.resolve(root), state_mod.AppState(model=SPEC))


class TestFirstRun:
    def test_first_launch_creates_configs_and_reports_no_models(self, tmp_path: Path) -> None:
        """The user opens the app for the very first time on a machine with
        no reachable providers: the state dir and configs appear, and the
        launch explains where models would come from instead of opening."""
        state = tmp_path / "state"
        terminal = Terminal(
            str(state),
            env={
                "HOME": str(tmp_path),  # no ~/.omlx to autodetect
                "OLLAMA_HOST": "127.0.0.1:9",  # nothing listens there
            },
        )
        terminal.expect("Created", "config.toml")
        terminal.expect("No models reachable right now.")
        assert terminal.wait() == 0
        config = (state / "configs" / "config.toml").read_text()
        for section in ("[providers.ollama]", "[providers.omlx]", "[providers.koboldcpp]"):
            assert section in config
        assert (state / "configs" / "prompts.toml").exists()


class TestChat:
    def test_pick_a_model_then_play_then_undo_with_ctrl_u(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """The user launches with no remembered model, picks one in the
        picker, plays a turn, and Ctrl+U takes it back."""
        state = tmp_path / "state"
        set_config_provider(state, server)
        terminal = Terminal(str(state))
        terminal.expect("Models (1)", "test-model")  # the picker, on screen
        terminal.send(ENTER, 1.0)
        terminal.expect("otaku")  # the banner — we are in the REPL
        terminal.send("I enter the hall.")
        terminal.send(ENTER, 1.0)
        terminal.expect("stirred")  # the reply streamed to the screen
        terminal.settle()  # the prompt must be back before a control key
        terminal.send(CTRL_U, 1.0)
        terminal.expect("Undone.")
        assert terminal.quit() == 0

    def test_a_remembered_story_resumes_on_launch(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """Closing and reopening the app lands mid-scene in the same story."""
        state = tmp_path / "state"
        set_config_provider(state, server)
        remember(state)
        first = Terminal(str(state))
        first.expect("otaku")
        first.send("I enter the hall.")
        first.send(ENTER, 1.0)
        first.expect("stirred")
        assert first.quit() == 0

        second = Terminal(str(state))
        second.expect("Resumed at message 2.", scripted.CHAT_REPLY.split()[0])
        assert second.quit() == 0


class TestShortcuts:
    def launch(self, server: ModelServer, tmp_path: Path) -> Terminal:
        state = tmp_path / "state"
        set_config_provider(state, server)
        remember(state)
        terminal = Terminal(str(state))
        terminal.expect("otaku")  # the banner — we are in the REPL
        return terminal

    def test_ctrl_r_regenerates_the_reply(self, server: ModelServer, tmp_path: Path) -> None:
        """A fresh take on the same prompt: Ctrl+R at the prompt streams a
        different reply in place of the old one."""
        terminal = self.launch(server, tmp_path)
        terminal.send("I roll the dice.")
        terminal.send(ENTER, 1.0)
        terminal.expect("stirred")
        server.script = lambda body: "It came up six."  # the model changes its mind
        terminal.settle()
        terminal.send(CTRL_R, 1.0)
        terminal.expect("It came up six.")
        assert terminal.quit() == 0

    def test_ctrl_t_browses_and_resumes_the_story(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """The story browser on screen: Ctrl+T opens it over the session,
        Enter drills into the messages, Enter resumes."""
        terminal = self.launch(server, tmp_path)
        terminal.send("I enter the hall.")
        terminal.send(ENTER, 1.0)
        terminal.expect("stirred")
        terminal.settle()
        terminal.send(CTRL_T, 1.0)
        terminal.expect("Stories (1)")
        terminal.send(ENTER, 1.0)
        terminal.expect("messages")  # the message view's header
        terminal.send(ENTER, 1.0)
        terminal.expect("Resumed at message 2.")
        assert terminal.quit() == 0

    def test_ctrl_o_opens_the_picker_over_the_session(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """Ctrl+O opens the model picker mid-session; picking the current
        model lands back at the prompt with nothing changed."""
        terminal = self.launch(server, tmp_path)
        terminal.settle()
        terminal.send(CTRL_O, 1.0)
        terminal.expect("Models (1)", "test-model")
        terminal.send(ENTER, 1.0)
        terminal.expect("Already using test/test-model.")
        assert terminal.quit() == 0


class TestHelpVersion:
    def test_version_prints_and_exits(self, tmp_path: Path) -> None:
        result = run_otaku(tmp_path / "state", "--version")
        assert result.returncode == 0
        assert "otaku" in result.stdout

    def test_help_names_the_subcommands(self, tmp_path: Path) -> None:
        result = run_otaku(tmp_path / "state", "--help")
        assert result.returncode == 0
        assert "logs" in result.stdout
