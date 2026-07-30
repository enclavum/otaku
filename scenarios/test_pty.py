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
from scenarios.support.server import ModelServer
from scenarios.support.terminal import CTRL_U, ENTER, Terminal


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
