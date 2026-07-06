"""Tests for REPL multiline input.

`LineAssembler` (the Ollama-style `\"\"\"` state machine) is tested directly;
`run()` is driven end-to-end through a prompt_toolkit pipe input to prove the
loop assembles and submits correctly.
"""

from __future__ import annotations

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from otaku.chat import repl
from otaku.chat.repl import LineAssembler, _cut_suffix
from otaku.config import Config, Encryption
from otaku.storage.store import Message, Store
from tests.support import make_provider


class TestCutSuffix:
    def test_present(self) -> None:
        assert _cut_suffix('abc"""', '"""') == ("abc", True)

    def test_absent(self) -> None:
        assert _cut_suffix("abc", '"""') == ("abc", False)

    def test_empty(self) -> None:
        assert _cut_suffix("", '"""') == ("", False)

    def test_only_suffix(self) -> None:
        assert _cut_suffix('"""', '"""') == ("", True)


class TestLineAssemblerNormal:
    def test_plain_line_passthrough(self) -> None:
        assert LineAssembler().feed("hello world") == ("hello world", False)

    def test_slash_line_is_normal(self) -> None:
        # a bare command line is returned unstripped, not raw → caller dispatches
        assert LineAssembler().feed("/clear") == ("/clear", False)

    def test_trailing_triple_without_prefix_is_literal(self) -> None:
        assert LineAssembler().feed('abc"""') == ('abc"""', False)


class TestLineAssemblerSingleLineBlock:
    def test_single_line_block(self) -> None:
        assert LineAssembler().feed('"""hello"""') == ("hello", True)

    def test_empty_block(self) -> None:
        assert LineAssembler().feed('""""""') == ("", True)

    def test_whitespace_preserved(self) -> None:
        assert LineAssembler().feed('"""  spaced  """') == ("  spaced  ", True)

    def test_wrapped_command_is_raw(self) -> None:
        # """/clear""" is a literal message, not a command
        assert LineAssembler().feed('"""/clear"""') == ("/clear", True)


class TestLineAssemblerMultiLineBlock:
    def test_two_line_block(self) -> None:
        a = LineAssembler()
        assert a.feed('"""line one') is None
        assert a.in_block is True
        assert a.feed('line two"""') == ("line one\nline two", True)
        assert a.in_block is False

    def test_three_line_block(self) -> None:
        a = LineAssembler()
        assert a.feed('"""a') is None
        assert a.feed("b") is None
        assert a.feed('c"""') == ("a\nb\nc", True)

    def test_opening_delimiter_alone_preserves_leading_newline(self) -> None:
        a = LineAssembler()
        assert a.feed('"""') is None
        assert a.feed('body"""') == ("\nbody", True)

    def test_mid_line_triple_does_not_close(self) -> None:
        a = LineAssembler()
        assert a.feed('"""start') is None
        assert a.feed('mid"""more') is None  # """ not at end → stays open
        assert a.feed('end"""') == ('start\nmid"""more\nend', True)

    def test_reset_drops_partial_block(self) -> None:
        a = LineAssembler()
        a.feed('"""partial')
        assert a.in_block is True
        a.reset()
        assert a.in_block is False
        # a fresh line now starts clean
        assert a.feed("new") == ("new", False)


# ---------- end-to-end through prompt_toolkit ----------


def _make_state() -> repl.State:
    prov = make_provider(name="test")
    cfg = Config(
        database_url="sqlite:///x", providers={"test": prov}, encryption=Encryption("disk")
    )
    return repl.State(config=cfg, provider=prov, model="m", full_model="test/m")


def _drive(store: Store, keystrokes: str, monkeypatch) -> tuple[repl.State, list[Message]]:
    """Run the REPL against a scripted pipe input; capture submitted messages."""
    captured: list[Message] = []
    monkeypatch.setattr(repl, "run_inference", lambda st, s: captured.append(st.messages[-1]))
    state = _make_state()
    with create_pipe_input() as pipe:
        pipe.send_text(keystrokes)
        with create_app_session(input=pipe, output=DummyOutput()):
            repl.run(state, store)
    return state, captured


class TestRunEndToEnd:
    def test_single_line_message_submitted(self, store: Store, monkeypatch) -> None:
        _, captured = _drive(store, "hello world\n\x04", monkeypatch)
        assert captured == [Message("user", "hello world")]

    def test_multiline_block_submitted_as_one_message(self, store: Store, monkeypatch) -> None:
        _, captured = _drive(store, '"""line one\nline two"""\n\x04', monkeypatch)
        assert captured == [Message("user", "line one\nline two")]

    def test_triple_wrapped_command_sent_literally(self, store: Store, monkeypatch) -> None:
        # """/clear""" must be sent to the model, not executed as a command
        _, captured = _drive(store, '"""/clear"""\n\x04', monkeypatch)
        assert captured == [Message("user", "/clear")]

    def test_bare_command_is_dispatched_not_sent(self, store: Store, monkeypatch) -> None:
        captured: list[Message] = []
        monkeypatch.setattr(repl, "run_inference", lambda st, s: captured.append(st.messages[-1]))
        state = _make_state()
        state.messages = [Message("user", "x"), Message("assistant", "y")]
        with create_pipe_input() as pipe:
            pipe.send_text("/clear\n\x04")
            with create_app_session(input=pipe, output=DummyOutput()):
                repl.run(state, store)
        assert captured == []  # /clear handled internally
        assert state.messages == []  # conversation cleared

    def test_ctrl_c_discards_open_block(self, store: Store, monkeypatch) -> None:
        _, captured = _drive(store, '"""partial\n\x03\x04', monkeypatch)
        assert captured == []  # block abandoned, nothing submitted

    def test_shortcut_restores_typed_input(self, store: Store, monkeypatch) -> None:
        # type "hello", press Ctrl+T (/history — no-op on an empty store), keep
        # typing "world", submit: the input is restored, so the message is joined.
        _, captured = _drive(store, "hello\x14world\n\x04", monkeypatch)
        assert captured == [Message("user", "helloworld")]
