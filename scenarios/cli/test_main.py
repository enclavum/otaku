"""End-to-end stories: the real binary in a pty, driven by keystrokes.

These are the literal user stories — slower than the in-process kind, so
only journeys that need the real REPL (keybindings, pickers on screen,
process lifecycle) live here.
"""

from pathlib import Path

from otaku.paths import Paths
from otaku.settings import state as state_mod
from otaku.terminal import PROMPT_CONTINUATION
from scenarios.support import server as scripted
from scenarios.support.harness import SPEC, run_otaku, set_config, set_config_provider
from scenarios.support.server import ModelServer
from scenarios.support.terminal import CTRL_O, CTRL_R, CTRL_T, CTRL_U, ENTER, Terminal


class TestFirstRun:
    def test_first_launch_without_a_model_opens_the_sample(self, tmp_path: Path) -> None:
        """The very first launch on a machine with nothing running: the
        state dir and configs appear, the picker has nothing to offer —
        and the app opens anyway, landing mid-story in the sample with no
        model selected. A turn explains itself instead of failing."""
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
        terminal.expect("Imported 14 message(s)")
        terminal.expect("You're late, mapmaker.")  # resumed mid-scene
        terminal.expect("A sample story was imported")
        terminal.send("Hello?")
        terminal.send(ENTER, 1.0)
        terminal.expect("No model selected")
        assert terminal.quit() == 0
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
        play(terminal, "I enter the hall.", "stirred")
        terminal.settle()  # the prompt must be back before a control key
        terminal.send(CTRL_U, 1.0)
        terminal.expect("Undone.")
        assert terminal.quit() == 0

    def test_undo_erases_the_exchange_when_the_terminal_answers(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """On a terminal that reports its cursor, Ctrl+U takes the whole
        exchange off the screen — block, blank, and reply — and says
        nothing: the vanishing is the report. The story really moved back:
        the next turn's context no longer carries the undone one."""
        terminal = launch_remembered(server, tmp_path / "state")
        play(terminal, "I enter the hall.", "stirred")
        terminal.settle()
        terminal.arm_cpr(20)  # the exchange sits well inside a 24-row screen
        terminal.send(CTRL_U, 1.0)
        terminal.settle()
        assert b"\x1b[6n" in terminal.raw  # the app asked the terminal
        # Block(1) + blank(1) + reply(1) + the standing gap(1): four rows up.
        assert b"\x1b[4A\r\x1b[J" in terminal.raw
        assert "Undone" not in terminal.transcript
        assert "undone" not in terminal.transcript
        server.script = lambda body: "A different dawn."
        play(terminal, "A fresh start.", "A different dawn.")
        sent = server.requests[-1]["messages"]
        assert not any("I enter the hall." in str(m) for m in sent)
        assert terminal.quit() == 0

    def test_undo_falls_back_after_another_command_intervened(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """/help printed below the exchange, so the screen cannot be
        restored by erasing — Ctrl+U reports the new ending instead, even
        though the terminal would have answered the cursor query."""
        terminal = launch_remembered(server, tmp_path / "state")
        play(terminal, "The first turn.", "stirred")
        terminal.settle()
        play(terminal, "/help", "Playing:")
        # One blank line between the typed command line and its output.
        assert "\r\n\r\nPlaying:" in terminal.transcript
        terminal.settle()
        terminal.arm_cpr(20)
        terminal.send(CTRL_U, 1.0)
        terminal.expect("Undone.")  # the story emptied — reported, not erased
        assert terminal.quit() == 0

    def test_regen_after_an_undo_report_erases_the_reechoed_reply(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """The turns an /undo report re-echoes are turns: Ctrl+R right
        after clears the re-echoed response and streams the fresh take in
        its place, the request still displayed — no marker. The report
        line above stays."""
        terminal = launch_remembered(server, tmp_path / "state")
        play(terminal, "The first turn.", "stirred")
        server.script = lambda body: "The dice settle slowly."
        terminal.settle()
        play(terminal, "The second turn.", "settle slowly")
        terminal.settle()
        play(terminal, "/info", "State dir:")  # prints below — undo must report
        terminal.settle()
        terminal.send(CTRL_U, 1.0)
        terminal.expect("the story now ends with")
        server.script = lambda body: "It came up six."
        terminal.settle()
        terminal.arm_cpr(20)
        terminal.send(CTRL_R, 1.0)
        terminal.expect("It came up six.")
        # The re-echoed reply(1) + the standing gap(1): two rows up.
        assert b"\x1b[2A\r\x1b[J" in terminal.raw
        assert "[ regenerating ]" not in terminal.transcript
        assert terminal.quit() == 0

    def test_at_pops_the_path_menu_and_enter_imports_the_pick(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """Typing @ in /import's FILE argument pops the path menu at once —
        no Tab — and Enter on the highlighted file completes and submits;
        the @ never reaches the handler."""
        tale = tmp_path / "tale.txt"
        tale.write_text("First beat.", encoding="utf-8")
        terminal = launch_remembered(server, tmp_path / "state")
        terminal.send(f"/import @{tmp_path}/ta")
        terminal.expect("tale.txt")  # the menu, popped while typing
        terminal.send(ENTER, 1.0)
        terminal.expect("Imported 1 message(s)")
        assert terminal.quit() == 0

    def test_last_reechoes_the_scene_and_its_turns_clear_again(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """/last brings the recent exchanges back after command litter —
        and hands them to the ledger: Ctrl+U right after erases the top
        re-echoed exchange silently, like a freshly played one."""
        terminal = launch_remembered(server, tmp_path / "state")
        play(terminal, "The first turn.", "stirred")
        server.script = lambda body: "The dice settle slowly."
        terminal.settle()
        play(terminal, "The second turn.", "settle slowly")
        terminal.settle()
        play(terminal, "/info", "State dir:")  # litter below the turns
        terminal.settle()
        before = terminal.transcript.count("> The first turn.")
        terminal.send("/last")
        terminal.send(ENTER, 1.0)
        assert terminal.transcript.count("> The first turn.") == before + 1
        terminal.settle()
        terminal.arm_cpr(20)
        terminal.send(CTRL_U, 1.0)
        terminal.settle()
        assert b"\x1b[4A\r\x1b[J" in terminal.raw  # the re-echo erased
        assert "undone" not in terminal.transcript  # silently — turns remain shown
        assert terminal.quit() == 0
        """Resume, undo through the echoed turns and keep going: each
        /undo past them replaces report and re-echo with a fresh report
        of the earlier exchange — one current report on screen, never one
        over nothing, never two stacked — down to the empty story."""
        state = tmp_path / "state"
        first = launch_remembered(server, state)
        for n in range(1, 6):
            server.script = lambda body, n=n: f"Reply number {n}."
            play(first, f"Turn number {n}.", f"Reply number {n}.")
        assert first.quit() == 0

        second = Terminal(str(state))
        second.expect("Resumed at message 10.")
        second.settle()
        second.arm_cpr(20)
        second.send(CTRL_U, 1.0)  # erases echoed exchange 5, silently
        second.settle()
        second.arm_cpr(20)
        second.send(CTRL_U, 1.0)  # erases echoed exchange 4, silently
        second.settle()
        second.send(CTRL_U, 1.0)  # nothing shown to erase — report
        second.expect("the story now ends with")
        second.settle()
        second.arm_cpr(20)
        second.send(CTRL_U, 1.0)  # takes re-echo AND report — fresh report
        second.expect("Reply number 1.")  # …now showing the earlier exchange
        assert second.transcript.count("the story now ends with") == 2
        # Report(1) + blank(1) + block(1) + blank(1) + reply(1) + gap(1).
        assert b"\x1b[6A\r\x1b[J" in second.raw
        second.settle()
        second.arm_cpr(20)
        second.send(CTRL_U, 1.0)  # the last exchange goes — empty, in place
        second.expect("The story is now empty")
        assert second.transcript.count("the story now ends with") == 2
        assert second.quit() == 0

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

    def test_resumed_turns_can_be_undone_off_the_screen(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """A relaunch lands mid-scene — and Ctrl+U takes the shown
        exchange back like a played one: the screen erases with no
        report, and the story head really moves."""
        terminal = relaunch_mid_story(server, tmp_path / "state", "I enter the hall.")
        terminal.settle()
        terminal.arm_cpr(20)
        terminal.send(CTRL_U, 1.0)
        terminal.settle()
        # Block(1) + blank(1) + reply(1) + the standing gap(1): four rows.
        assert b"\x1b[4A\r\x1b[J" in terminal.raw
        assert "Undone" not in terminal.transcript
        assert "undone" not in terminal.transcript
        server.script = lambda body: "A different dawn."
        play(terminal, "A fresh start.", "A different dawn.")
        sent = server.requests[-1]["messages"]
        assert not any("I enter the hall." in str(m) for m in sent)
        assert terminal.quit() == 0

    def test_a_resumed_reply_regenerates_in_place(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """Ctrl+R right after a relaunch: the shown reply is erased and
        the fresh take streams where it stood — no marker."""
        terminal = relaunch_mid_story(server, tmp_path / "state", "I roll the dice.")
        server.script = lambda body: "It came up six."
        terminal.settle()
        terminal.arm_cpr(20)
        terminal.send(CTRL_R, 1.0)
        terminal.expect("It came up six.")
        # Reply(1) + the standing gap(1): two rows up, and no marker.
        assert b"\x1b[2A\r\x1b[J" in terminal.raw
        assert "[ regenerating ]" not in terminal.transcript
        assert terminal.quit() == 0

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


class TestShortcuts:
    def test_ctrl_r_regenerates_the_reply(self, server: ModelServer, tmp_path: Path) -> None:
        """A fresh take on the same prompt: Ctrl+R at the prompt streams a
        different reply in place of the old one."""
        terminal = launch_remembered(server, tmp_path / "state")
        play(terminal, "I roll the dice.", "stirred")
        server.script = lambda body: "It came up six."  # the model changes its mind
        terminal.settle()
        terminal.send(CTRL_R, 1.0)
        terminal.expect("It came up six.")
        assert terminal.quit() == 0

    def test_a_fallback_regen_echoes_the_prompt_it_reruns(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """/info printed below the reply, so Ctrl+R announces — with the
        prompt re-echoed under the marker, like an undo report shows its
        turns. The new take reads as an exchange: the next Ctrl+R erases
        its reply in place, and Ctrl+U takes marker and echo into an undo
        report."""
        terminal = launch_remembered(server, tmp_path / "state")
        play(terminal, "I roll the dice.", "stirred")
        terminal.settle()
        play(terminal, "/info", "State dir:")  # prints below — regen must announce
        server.script = lambda body: "It came up six."
        terminal.settle()
        before = terminal.transcript.count("> I roll the dice.")
        terminal.send(CTRL_R, 1.0)
        terminal.expect("It came up six.")
        assert "[ regenerating ]" in terminal.transcript
        assert terminal.transcript.count("> I roll the dice.") == before + 1  # re-echoed
        server.script = lambda body: "It came up one."
        terminal.settle()
        terminal.arm_cpr(20)
        terminal.send(CTRL_R, 1.0)  # the echo is a live exchange — in place
        terminal.expect("It came up one.")
        assert terminal.transcript.count("[ regenerating ]") == 1
        assert b"\x1b[2A\r\x1b[J" in terminal.raw
        terminal.settle()
        terminal.arm_cpr(20)
        terminal.send(CTRL_U, 1.0)  # marker and echo go — the report replaces them
        terminal.expect("The story is now empty")
        assert b"\x1b[6A\r\x1b[J" in terminal.raw
        assert terminal.quit() == 0

    def test_ctrl_r_erases_the_old_reply_when_the_terminal_answers(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """With the cursor known, Ctrl+R does not announce itself: the old
        reply vanishes and the fresh take streams in its place."""
        terminal = launch_remembered(server, tmp_path / "state")
        play(terminal, "I roll the dice.", "stirred")
        server.script = lambda body: "It came up six."
        terminal.settle()
        terminal.arm_cpr(20)
        terminal.send(CTRL_R, 1.0)
        terminal.expect("It came up six.")
        # Reply(1) + the standing gap(1): two rows up, and no marker.
        assert b"\x1b[2A\r\x1b[J" in terminal.raw
        assert "[ regenerating ]" not in terminal.transcript
        assert terminal.quit() == 0

    def test_ctrl_t_browses_and_resumes_the_story(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """The story browser on screen: Ctrl+T opens it over the session,
        Enter drills into the messages, Enter resumes."""
        terminal = launch_remembered(server, tmp_path / "state")
        play(terminal, "I enter the hall.", "stirred")
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
        terminal = launch_remembered(server, tmp_path / "state")
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


def relaunch_mid_story(server: ModelServer, root: Path, line: str) -> Terminal:
    """Play one turn, close, reopen: a terminal resumed mid-scene, its
    last turns echoed on screen."""
    first = launch_remembered(server, root)
    play(first, line, "stirred")
    assert first.quit() == 0
    second = Terminal(str(root))
    second.expect("Resumed at message 2.")
    return second
