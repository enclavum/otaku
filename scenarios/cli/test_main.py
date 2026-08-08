"""End-to-end stories: the real binary in a pty, driven by keystrokes.

These are the literal user stories — slower than the in-process kind, so
only journeys that need the real REPL (keybindings, pickers on screen,
process lifecycle) live here.
"""

import socket
from pathlib import Path

import pytest

from otaku.paths import Paths
from otaku.settings import state as state_mod
from otaku.terminal import PROMPT_CONTINUATION
from scenarios.support import server as scripted
from scenarios.support.harness import SPEC, run_otaku, set_config, set_config_provider
from scenarios.support.server import ModelServer
from scenarios.support.terminal import CTRL_R, ENTER, ESC, Terminal


class TestFirstRun:
    def test_first_launch_without_a_model_opens_the_sample(self, tmp_path: Path) -> None:
        """The very first launch on a machine with nothing running: the
        state dir and configs appear, the picker has nothing to offer —
        and the app opens anyway, landing mid-story in the sample with no
        model selected. A turn explains itself instead of failing. The
        premise is an empty machine: a real engine on a fixed default
        port cannot be isolated by env, so the story skips instead."""
        for port in (8000, 8080, 5001, 1234):  # omlx, llama.cpp, kobold, LM Studio
            if _listening(port):
                pytest.skip(f"a real engine answers on :{port} — this story needs a quiet machine")
        state = tmp_path / "state"
        terminal = Terminal(
            str(state),
            env={
                "HOME": str(tmp_path),  # no ~/.omlx to autodetect
                "OLLAMA_HOST": "127.0.0.1:9",  # nothing listens there
            },
        )
        terminal.expect("Created", "config.toml")
        terminal.expect("Models (0)")  # the empty picker: the panel is the door
        terminal.send(ESC, 1.0)
        terminal.expect("Imported 14 message(s)")
        terminal.expect("You're late, mapmaker.")  # resumed mid-scene
        terminal.expect("A sample story was imported")
        terminal.send("Hello?")
        terminal.send(ENTER, 1.0)
        terminal.expect("No model selected")
        assert terminal.quit() == 0
        assert "[providers." not in (state / "configs" / "config.toml").read_text()
        providers = (state / "configs" / "providers.toml").read_text()
        for section in ("[llamacpp]", "[koboldcpp]", "[ollama]", "[omlx]", "[lmstudio]"):
            assert section in providers
        assert (state / "configs" / "prompts.toml").exists()


class TestChat:
    def test_a_remembered_story_resumes_on_launch(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """Closing and reopening the app lands mid-scene in the same story."""
        first = launch_remembered(server, tmp_path / "state")
        play(first, "I enter the hall.", "stirred")
        assert first.quit() == 0

        second = Terminal(str(tmp_path / "state"))
        second.expect("Resumed at message 2.", scripted.CHAT_REPLY.split()[0])
        assert second.quit() == 0

    def test_the_install_journey_lands_mid_story(self, server: ModelServer, tmp_path: Path) -> None:
        """The whole first-run scenario: install, launch, pick a model —
        and you are in the middle of a playable story, ready to explore."""
        state = tmp_path / "state"
        set_config_provider(state, server)
        set_config(state, seed_sample=True)
        terminal = Terminal(str(state))
        terminal.expect("Models (1)", "test-model")
        terminal.send(ENTER, 1.0)
        terminal.expect("Imported 14 message(s)")
        terminal.expect("You're late, mapmaker.")  # resumed mid-scene
        terminal.expect("A sample story was imported")
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
        # Descriptions print in full on one line — never shrunk to "..." —
        # and the commands list in declaration order, not alphabetically.
        listing = run_otaku(tmp_path / "state", "logs", "--help").stdout
        commands_block = listing.split("Commands:", 1)[1]
        assert "..." not in commands_block
        assert "Show every contained crash's traceback" in commands_block
        order = [commands_block.index(name) for name in ("requests", "system", "error")]
        assert order == sorted(order)


class TestStreaming:
    def test_ctrl_r_mid_stream_cancels_and_regenerates(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """Ctrl+R while the reply is still streaming: the stream stops and
        a fresh take begins — no waiting for the rest."""
        terminal = launch_remembered(server, tmp_path / "state")
        server.chunk_delay = 0.4  # a slow model — the reply arrives in beats
        server.script = lambda body: "The corridor stretches on, deeper into the dark."
        terminal.send("I walk the corridor.")
        terminal.send(ENTER, 0.2)
        terminal.expect("The corridor")  # streaming has begun
        server.script = lambda body: "It came up six."
        terminal.send(CTRL_R, 0.5)
        terminal.expect("It came up six.")
        terminal.settle()
        assert terminal.quit() == 0

    def test_a_triple_quoted_block_sends_one_message(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """The multiline convention: an opening triple quote collects lines
        until the closing one, and everything between goes as ONE message,
        newlines preserved, never dispatched as a command."""
        terminal = launch_remembered(server, tmp_path / "state")
        terminal.send('"""')
        terminal.send(ENTER, 0.3)
        terminal.expect(PROMPT_CONTINUATION)
        terminal.send("/regen is part of my story")
        terminal.send(ENTER, 0.3)
        terminal.send('and so is this line"""')
        terminal.send(ENTER, 1.0)
        terminal.expect("stirred")
        sent = str(server.requests[-1]["messages"][-1]["content"])
        assert "/regen is part of my story\nand so is this line" in sent
        assert terminal.quit() == 0


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def remember(root: Path) -> None:
    """state.toml pointing at the scripted model, so launch lands in the
    REPL instead of the picker."""
    state_mod.save(Paths.resolve(root), state_mod.AppState(model=SPEC))


def launch_remembered(server: ModelServer, root: Path) -> Terminal:
    """A terminal already in the REPL: the scripted provider configured,
    the model remembered, the picker skipped."""
    set_config_provider(root, server)
    remember(root)
    terminal = Terminal(str(root))
    terminal.expect("otaku")  # the banner — we are in the REPL
    return terminal


def play(terminal: Terminal, line: str, marker: str) -> None:
    """Send one prompt line and wait until `marker` shows on screen."""
    terminal.send(line)
    terminal.send(ENTER, 1.0)
    terminal.expect(marker)
