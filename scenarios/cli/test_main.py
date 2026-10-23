"""End-to-end stories: the real binary in a pty, driven by keystrokes.

These are the literal user stories — slower than the in-process kind, so
only journeys that need the real REPL (keybindings, pickers on screen,
process lifecycle) live here.
"""

import signal
import socket
from collections.abc import Callable
from pathlib import Path

import pytest

from otaku.backend.api import Api
from otaku.backend.app.paths import Paths
from otaku.backend.launch import open_app
from otaku.settings import state as state_mod
from otaku.terminal.prompt.session import CHAT_PLACEHOLDER
from otaku.terminal.tty import PROMPT_CONTINUATION
from scenarios.support import server as scripted
from scenarios.support.harness import SPEC, run_otaku, set_config, set_config_provider
from scenarios.support.server import ModelServer
from scenarios.support.terminal import CTRL_D, CTRL_R, ENTER, ESC, TAB, Terminal

pytestmark = pytest.mark.cli

# The continuation prompt as the transcript shows it: its trailing space is
# not printed, the caret moved past it, with no placeholder drawn after it.
_CONTINUED = PROMPT_CONTINUATION.rstrip()
_PLACEHOLDER = "".join(text for _, text in CHAT_PLACEHOLDER)


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
        # The seeding is silent (its counts are not launch chrome); the
        # story itself is the proof, resumed mid-scene.
        terminal.expect("You're late, mapmaker.")
        terminal.expect("Sample stories were imported")
        terminal.send("Hello?")
        terminal.send(ENTER, 1.0)
        terminal.expect("No model selected")
        assert terminal.quit() == 0
        assert "[providers." not in (state / "configs" / "config.toml").read_text(encoding="utf-8")
        providers = (state / "configs" / "providers.toml").read_text(encoding="utf-8")
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
        # Seeding both samples (one of them 300+ messages) runs before
        # the picker can draw, so the first screen earns a longer wait.
        # The header's count region repaints in place, so the raw
        # transcript may hold "Models (1)" split by cursor moves — the
        # word and the row are the anchors that survive redraws.
        terminal.expect("Models", "test-model", timeout=30.0)
        terminal.send(ENTER, 1.0)
        # The seeding is silent (its counts are not launch chrome); the
        # story itself is the proof, resumed mid-scene.
        terminal.expect("You're late, mapmaker.")
        terminal.expect("Sample stories were imported")
        assert terminal.quit() == 0


class TestServe:
    def test_ctrl_r_serves_again_and_ctrl_d_stops(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """`otaku serve` answers the keys at the terminal it runs in: Ctrl+R
        stops and comes back on fresh sources — a new process, the
        address said again — and Ctrl+D stops for good, as Ctrl+C does."""
        state = tmp_path / "state"
        set_config_provider(state, server)
        terminal = Terminal(str(state), args=("serve",), env={"OTAKU_WEB_PORT": str(_free_port())})
        terminal.expect("Running on", timeout=20.0)
        _pressed_until(terminal, CTRL_R, "Restarting", timeout=20.0)
        _expect_count(terminal, "Running on", 2, timeout=20.0)
        _pressed_until(terminal, CTRL_D, "Shutting down", timeout=20.0)
        assert terminal.wait() == 0

    def test_the_old_name_still_serves(self, server: ModelServer, tmp_path: Path) -> None:
        """`otaku web`, the command's name before 0.9.0, still serves."""
        state = tmp_path / "state"
        set_config_provider(state, server)
        port = _free_port()
        terminal = Terminal(str(state), args=("web",), env={"OTAKU_WEB_PORT": str(port)})
        _waited(terminal, lambda: _listening(port))
        _pressed_until(terminal, CTRL_D, "Shutting down", timeout=20.0)
        assert terminal.wait() == 0

    def test_set_serve_serves_beside_the_chat_and_off_stops_it(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """`/set serve on` serves the page and the chat keeps its prompt —
        a line plays while it serves; `/set serve off` stops it, and the
        chat carries on."""
        port = _free_port()
        terminal = launch_remembered(server, tmp_path / "state", env={"OTAKU_WEB_PORT": str(port)})
        terminal.send("/set serve on")
        terminal.send(ENTER, 1.0)
        _waited(terminal, lambda: _listening(port))
        play(terminal, "I enter the hall.", "stirred")
        terminal.send("/set serve off")
        terminal.send(ENTER, 1.0)
        _waited(terminal, lambda: not _listening(port))
        play(terminal, "I look around.", "stirred")
        assert terminal.quit() == 0

    def test_a_launch_with_the_switch_on_serves_until_the_chat_ends(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        state = tmp_path / "state"
        set_config(state, web_terminal_serve=True)
        port = _free_port()
        terminal = launch_remembered(server, state, env={"OTAKU_WEB_PORT": str(port)})
        _waited(terminal, lambda: _listening(port))
        assert terminal.quit() == 0
        assert not _listening(port)


class TestSigterm:
    """SIGTERM — a `docker stop`, a `kill` — is an orderly stop in both
    frontends: the chat quits as /quit does, a reply streaming kept as far
    as it came; `otaku serve` shuts down as at the first Ctrl+C."""

    def test_at_the_prompt_the_chat_quits(self, server: ModelServer, tmp_path: Path) -> None:
        terminal = launch_remembered(server, tmp_path / "state")
        terminal.settle()
        terminal.send_signal(signal.SIGTERM)
        assert terminal.wait() == 0

    def test_mid_reply_what_streamed_is_kept(self, server: ModelServer, tmp_path: Path) -> None:
        state = tmp_path / "state"
        terminal = launch_remembered(server, state)
        server.chunk_delay = 0.4  # a slow model — the reply arrives in beats
        server.script = lambda body: "The corridor stretches on, deeper into the dark."
        terminal.send("I walk the corridor.")
        terminal.send(ENTER, 0.2)
        terminal.expect("The corridor")  # streaming has begun
        terminal.send_signal(signal.SIGTERM)
        assert terminal.wait() == 0
        app = open_app(state)
        try:
            api = Api(app)
            story = api.settings.get().last_story
            assert story is not None
            reply = api.stories.messages.list(story)[-1]
            assert reply.role == "assistant"
            assert reply.body.startswith("The corridor")
        finally:
            app.close()

    def test_otaku_serve_shuts_down(self, server: ModelServer, tmp_path: Path) -> None:
        state = tmp_path / "state"
        set_config_provider(state, server)
        port = _free_port()
        terminal = Terminal(str(state), args=("serve",), env={"OTAKU_WEB_PORT": str(port)})
        _waited(terminal, lambda: _listening(port))
        terminal.send_signal(signal.SIGTERM)
        assert terminal.wait() == 0
        assert not _listening(port)


class TestUninstall:
    """`otaku uninstall` asks before it deletes anything, and never where no
    one can answer; from a checkout, which the reader removes, it offers
    the purge alone."""

    def test_piped_it_refuses_and_touches_nothing(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        state = tmp_path / "state"
        set_config_provider(state, server)
        result = run_otaku(state, "uninstall")
        assert result.returncode == 1
        assert (state / "configs").is_dir()

    def test_from_a_checkout_the_purge_alone_is_offered_and_kept_by_default(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        # The suite runs otaku from its own checkout, which the reader removes.
        state = tmp_path / "state"
        set_config_provider(state, server)
        terminal = Terminal(str(state), args=("uninstall",))
        terminal.expect("Purge all data?")
        terminal.send(ENTER)
        assert terminal.wait() == 0
        assert (state / "configs").is_dir()

    def test_a_purge_confirmed_deletes_the_state_dir(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        state = tmp_path / "state"
        set_config_provider(state, server)
        terminal = Terminal(str(state), args=("uninstall",))
        terminal.expect("Purge all data?")
        terminal.send("y")
        terminal.send(ENTER)
        terminal.expect("Are you sure")
        terminal.send("y")
        terminal.send(ENTER)
        assert terminal.wait() == 0
        assert not state.exists()


class TestHelpVersion:
    def test_version_prints_and_exits(self, tmp_path: Path) -> None:
        result = run_otaku(tmp_path / "state", "--version")
        assert result.returncode == 0
        assert "otaku" in result.stdout

    def test_help_names_serve_and_keeps_its_old_name_out(self, tmp_path: Path) -> None:
        listing = run_otaku(tmp_path / "state", "--help").stdout.split("Commands:", 1)[1]
        names = [row.split()[0] for row in listing.splitlines() if row.strip()]
        assert "serve" in names
        assert "web" not in names
        assert run_otaku(tmp_path / "state", "web", "--help").returncode == 0

    def test_help_names_the_subcommands(self, tmp_path: Path) -> None:
        result = run_otaku(tmp_path / "state", "--help")
        assert result.returncode == 0
        assert "logs" in result.stdout
        # Descriptions print in full on one line — never shrunk to "..." —
        # and the commands list in declaration order, not alphabetically.
        listing = run_otaku(tmp_path / "state", "logs", "--help").stdout
        commands_block = listing.split("Commands:", 1)[1]
        assert "..." not in commands_block
        assert "Show crash tracebacks" in commands_block
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


class TestErasing:
    def test_a_typed_regen_erases_from_an_unmoved_cursor(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """Issue #6: the cursor query guarding an erase must not read as
        command output — the separating blank it once triggered dropped
        the cursor a row right before the erase measured from it, and
        every TYPED /undo and /regen came up one row short, the reply's
        first line surviving (the shortcuts, which erase the typed line
        first, never arm that blank). The pin is the byte shape: the
        query runs straight into the three-row erase — typed line, gap,
        one-row reply — with nothing printed in between."""
        terminal = launch_remembered(server, tmp_path / "state")
        play(terminal, "I walk the corridor.", "stirred")
        server.script = lambda body: "It came up six."
        terminal.send("/regen")
        terminal.arm_cpr(20)
        terminal.send(ENTER, 1.0)
        terminal.expect("It came up six.")
        erase = terminal.raw.index(b"\x1b[6n\x1b[3A\r\x1b[J")
        assert not terminal.raw[:erase].endswith(b"\r\n")


class TestBlocks:
    """The `\"\"\"` convention: a way to press Enter without submitting, and
    nothing more. What it collects is an ordinary prompt — read for its
    framing syntax like any typed line and told apart from one by nothing
    downstream. Only the real REPL assembles it (`App.play` takes a line
    already assembled), so these are the only tests that reach the state
    machine at all, and each asserts what came off it on the wire.
    """

    def test_a_block_sends_one_message(self, server: ModelServer, tmp_path: Path) -> None:
        """An opening triple quote collects lines until the closing one, and
        everything between goes as ONE message, newlines preserved. A
        command written inside is text there: the message has begun, so
        nothing on a later line can dispatch."""
        terminal = launch_remembered(server, tmp_path / "state")
        terminal.send('"""')
        terminal.send(ENTER, 0.3)
        terminal.expect(_CONTINUED)
        terminal.send("/regen is part of my story")
        terminal.send(ENTER, 0.3)
        terminal.send('and so is this line"""')
        terminal.send(ENTER, 1.0)
        terminal.expect("stirred")
        sent = str(server.requests[-1]["messages"][-1]["content"])
        assert "/regen is part of my story\nand so is this line" in sent
        assert terminal.quit() == 0

    def test_a_block_plays_the_framing_its_first_line_opens(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """A command DOES open a block — the first position of the first
        line is where one lives — so `/me NAME:` frames every line the block
        collected, not just the one it was typed on."""
        terminal = launch_remembered(server, tmp_path / "state")
        terminal.send('"""/me Elara: I step into the hall.')
        terminal.send(ENTER, 0.3)
        terminal.expect(_CONTINUED)
        terminal.send('The torches have burned down."""')
        terminal.send(ENTER, 1.0)
        terminal.expect("stirred")
        sent = str(server.requests[-1]["messages"][-1]["content"])
        # The name reached the template, and the prefix did not reach the wire.
        assert "Elara" in sent
        assert not sent.startswith("/me")
        assert "I step into the hall.\nThe torches have burned down." in sent
        assert terminal.quit() == 0

    def test_an_inliner_completes_on_a_continuation_line(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """Inside a block the menu offers INLINERS, not commands: the
        message has already begun, so only something that closes a line can
        follow. `/c` completes to `/cue` — the command menu would have
        offered /cast, /clear and /context and completed none of them — and
        the cue reaches the wire in its enclosure, which is the only proof
        of which menu was open that is not a matter of appearance."""
        terminal = launch_remembered(server, tmp_path / "state")
        terminal.send('"""')
        terminal.send(ENTER, 0.3)
        terminal.expect(_CONTINUED)
        terminal.send("I step into the hall.")
        terminal.send(ENTER, 0.3)
        terminal.send("/c", 0.3)
        # The row itself is the synchronisation point — Tab before the menu
        # populates would type a literal tab and prove nothing.
        terminal.expect("/cue")
        terminal.send(TAB, 0.5)
        terminal.send('keep it short"""')
        terminal.send(ENTER, 1.0)
        terminal.expect("stirred")
        sent = str(server.requests[-1]["messages"][-1]["content"])
        assert "I step into the hall. ((OOC: keep it short))" in sent
        assert terminal.quit() == 0


def _free_port() -> int:
    """A port nothing is listening on, asked of the OS and let go."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _expect_count(terminal: Terminal, marker: str, count: int, *, timeout: float) -> None:
    """Wait until `marker` has shown `count` times — `expect` cannot tell
    a second showing from the first still on screen."""
    import time

    deadline = time.time() + timeout
    while terminal.transcript.count(marker) < count and time.time() < deadline:
        terminal.settle(0.5)
    assert terminal.transcript.count(marker) >= count, terminal.transcript[-2000:]


def _pressed_until(terminal: Terminal, key: bytes, marker: str, *, timeout: float) -> None:
    """Press `key` until `marker` shows. The address is said a moment
    before the keys are watched, and a key pressed in between is the
    terminal's own (Ctrl+R reprints the line), so a loaded machine may
    need it twice."""
    import time

    deadline = time.time() + timeout
    while marker not in terminal.transcript and time.time() < deadline:
        terminal.send(key, 1.0)
    assert marker in terminal.transcript, terminal.transcript[-2000:]


def _waited(terminal: Terminal, holds: Callable[[], bool], timeout: float = 20.0) -> None:
    """Wait until `holds` does — a port answering, or let go — reading the
    terminal meanwhile, so the app never blocks on a full pty, and showing
    its tail if it never comes."""
    import time

    deadline = time.time() + timeout
    while not holds():
        assert time.time() < deadline, f"never came; the terminal:\n{terminal.transcript[-1500:]}"
        terminal.settle(0.1)


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def remember(root: Path) -> None:
    """state.toml pointing at the scripted model, so launch lands in the
    REPL instead of the picker."""
    state_mod.save(Paths.resolve(root).state_file, state_mod.StateConfig(model=SPEC))


def launch_remembered(
    server: ModelServer, root: Path, *, env: dict[str, str] | None = None
) -> Terminal:
    """A terminal already in the REPL: the scripted provider configured,
    the model remembered, the picker skipped. `env` rides into the
    process, for a story that needs a variable set."""
    set_config_provider(root, server)
    remember(root)
    terminal = Terminal(str(root), env=env)
    terminal.expect("otaku")  # the banner — we are in the REPL
    return terminal


def play(terminal: Terminal, line: str, marker: str) -> None:
    """Send one prompt line and wait until `marker` shows on screen and
    the prompt is back: what is typed while a reply still streams is
    thrown away at its end, and its words show a moment before that."""
    import time

    prompts = terminal.transcript.count(_PLACEHOLDER)
    terminal.send(line)
    terminal.send(ENTER, 1.0)
    terminal.expect(marker)
    deadline = time.time() + 10.0
    while terminal.transcript.count(_PLACEHOLDER) <= prompts:
        assert time.time() < deadline, "the prompt never came back"
        terminal.settle(0.2)
