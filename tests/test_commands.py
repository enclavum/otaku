"""Tests for slash-command handlers, persistence, and the inference loop."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from otaku import config
from otaku.chat import commands, inference
from otaku.chat.commands import (
    apply_settings,
    cmd_bye,
    cmd_clear,
    cmd_copy,
    cmd_fork,
    cmd_help,
    cmd_model,
    cmd_new,
    cmd_print,
    cmd_regenerate,
    cmd_remember,
    cmd_save,
    cmd_set,
    cmd_title,
    cmd_undo,
    dispatch,
)
from otaku.chat.inference import (
    State,
    _has_real_turn,
    persist,
    run_inference,
    run_oneshot,
)
from otaku.client import ContentDelta, FinalStats
from otaku.config import Config, Encryption, Settings
from otaku.storage.store import Message, Store
from tests.support import make_provider


def make_state(*, supports_thinking: bool = False, messages: list[Message] | None = None) -> State:
    prov = make_provider(name="test", supports_thinking=supports_thinking)
    cfg = Config(
        database_url="sqlite:///x", providers={"test": prov}, encryption=Encryption("disk")
    )
    return State(
        config=cfg,
        provider=prov,
        model="m",
        full_model="test/m",
        messages=list(messages or []),
    )


class TestStreamWatcherPortability:
    def test_no_op_without_termios(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate Windows (no termios/tty): the watcher must be a safe no-op —
        # enter/exit without raising and without starting its reader thread.
        monkeypatch.setattr(inference, "termios", None)
        monkeypatch.setattr(inference, "tty", None)
        watcher = inference._StreamWatcher()
        with watcher:
            pass
        assert watcher._thread is None
        assert watcher.regen_requested is False


class TestHasRealTurn:
    def test_empty(self) -> None:
        assert _has_real_turn([]) is False

    def test_system_only(self) -> None:
        assert _has_real_turn([Message("system", "s")]) is False

    def test_with_user(self) -> None:
        assert _has_real_turn([Message("system", "s"), Message("user", "u")]) is True


class TestPersist:
    def test_no_conversation_created_without_real_turn(self, store: Store) -> None:
        state = make_state(messages=[Message("system", "s")])
        persist(state, store)
        assert state.conv_id is None
        assert store.list_conversations() == []

    def test_creates_and_snapshots_on_real_turn(self, store: Store) -> None:
        state = make_state(messages=[Message("user", "hi")])
        persist(state, store)
        assert state.conv_id is not None
        assert store.load_conversation(state.conv_id) == [Message("user", "hi")]


class TestClear:
    def test_keeps_system_and_stays_in_conversation(self, store: Store) -> None:
        state = make_state(
            messages=[Message("system", "s"), Message("user", "u"), Message("assistant", "a")]
        )
        cid = store.create_conversation("test/m")
        state.conv_id = cid
        cmd_clear(state, store, [])
        assert state.messages == [Message("system", "s")]
        assert state.conv_id == cid  # /clear stays in the same conversation

    def test_no_system(self, store: Store) -> None:
        state = make_state(messages=[Message("user", "u")])
        cmd_clear(state, store, [])
        assert state.messages == []


class TestNew:
    def test_clears_and_detaches(self, store: Store) -> None:
        state = make_state(
            messages=[Message("system", "s"), Message("user", "u"), Message("assistant", "a")]
        )
        state.conv_id = store.create_conversation("test/m")
        cmd_new(state, store, [])
        assert state.messages == [Message("system", "s")]
        assert state.conv_id is None  # /new starts a fresh conversation


class TestTitle:
    def test_sets_title(self, store: Store) -> None:
        state = make_state(messages=[Message("user", "q"), Message("assistant", "a")])
        cmd_title(state, store, ["My", "Great", "Chat"])
        assert state.conv_id is not None
        assert store.list_conversations()[0].title == "My Great Chat"

    def test_usage_without_text(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_title(make_state(messages=[Message("user", "q")]), store, [])
        assert "Usage: /title" in capsys.readouterr().out

    def test_nothing_to_title_when_empty(
        self, store: Store, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = make_state()  # no messages → no conversation to title
        cmd_title(state, store, ["hi"])
        assert "Nothing to title" in capsys.readouterr().out
        assert state.conv_id is None

    def test_confirmation(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_title(make_state(messages=[Message("user", "q")]), store, ["Name"])
        assert 'Title set: "Name"' in capsys.readouterr().out


class TestModel:
    def test_switch_by_spec(self, store: Store) -> None:
        state = make_state()  # provider "test", model "m"
        cmd_model(state, store, ["test/qwen"])
        assert state.model == "qwen"
        assert state.full_model == "test/qwen"
        assert config.read_last_model() == "test/qwen"  # remembered as last used

    def test_unknown_provider(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state()
        cmd_model(state, store, ["nope/x"])
        assert "Use PROVIDER/MODEL" in capsys.readouterr().out
        assert state.full_model == "test/m"  # unchanged
        assert config.read_last_model() is None  # failed switch is not remembered

    def test_already_using(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state()
        cmd_model(state, store, ["test/m"])
        assert "Already using" in capsys.readouterr().out
        assert config.read_last_model() is None  # no-op switch is not remembered

    def test_picker_switch(self, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            commands, "pick_model", lambda providers, initial_spec=None: "test/qwen"
        )
        state = make_state()
        cmd_model(state, store, [])
        assert state.full_model == "test/qwen"
        assert config.read_last_model() == "test/qwen"

    def test_picker_cancel_keeps_model(self, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(commands, "pick_model", lambda providers, initial_spec=None: None)
        state = make_state()
        cmd_model(state, store, [])
        assert state.full_model == "test/m"  # cancel keeps the current model


class TestUndo:
    def test_nothing_to_undo_when_empty(
        self, store: Store, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = make_state()
        cmd_undo(state, store, [])
        assert "Nothing to undo" in capsys.readouterr().out

    def test_pops_user_assistant_pair(self, store: Store) -> None:
        state = make_state(messages=[Message("user", "u"), Message("assistant", "a")])
        persist(state, store)
        cmd_undo(state, store, [])
        assert state.messages == []
        # last turn undone → row deleted, conv_id cleared
        assert state.conv_id is None
        assert store.list_conversations() == []

    def test_pops_orphan_user(self, store: Store) -> None:
        state = make_state(
            messages=[Message("user", "u1"), Message("assistant", "a1"), Message("user", "u2")]
        )
        persist(state, store)
        cmd_undo(state, store, [])
        assert state.messages == [Message("user", "u1"), Message("assistant", "a1")]
        assert state.conv_id is not None
        assert store.load_conversation(state.conv_id) == state.messages

    def test_assistant_without_preceding_user(
        self, store: Store, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = make_state(messages=[Message("system", "s"), Message("assistant", "a")])
        cmd_undo(state, store, [])
        assert "Nothing to undo" in capsys.readouterr().out


class TestRegenerate:
    def test_pops_and_reinfers(self, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(commands, "run_inference", lambda s, st: calls.append(True))
        state = make_state(messages=[Message("user", "u"), Message("assistant", "a")])
        persist(state, store)
        cmd_regenerate(state, store, [])
        assert state.messages == [Message("user", "u")]
        assert calls == [True]

    def test_nothing_to_regenerate(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state(messages=[Message("user", "u")])
        cmd_regenerate(state, store, [])
        assert "Nothing to regenerate" in capsys.readouterr().out


class TestFork:
    def test_nothing_to_fork_when_empty(
        self, store: Store, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = make_state()
        cmd_fork(state, store, [])
        assert "Nothing to fork" in capsys.readouterr().out

    def test_creates_new_conversation(self, store: Store) -> None:
        state = make_state(messages=[Message("user", "u"), Message("assistant", "a")])
        persist(state, store)
        original = state.conv_id
        cmd_fork(state, store, [])
        assert state.conv_id != original
        assert len(store.list_conversations()) == 2


class TestSetSystem:
    def test_sets_system_prompt(self, store: Store) -> None:
        state = make_state(messages=[Message("user", "u")])
        cmd_set(state, store, ["system", "be", "terse"])
        assert state.messages[0] == Message("system", "be terse")

    def test_replaces_existing_system(self, store: Store) -> None:
        state = make_state(messages=[Message("system", "old"), Message("user", "u")])
        cmd_set(state, store, ["system", "new"])
        assert state.messages[0] == Message("system", "new")
        assert state.messages[1] == Message("user", "u")

    def test_empty_shows_current(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state(messages=[Message("system", "hello")])
        cmd_set(state, store, ["system"])
        assert 'System: "hello"' in capsys.readouterr().out


class TestSetThink:
    def test_on_aliases_medium(self, store: Store) -> None:
        state = make_state(supports_thinking=True)
        cmd_set(state, store, ["think", "on"])
        assert state.think == "medium"

    def test_off_aliases_none(self, store: Store) -> None:
        state = make_state(supports_thinking=True)
        cmd_set(state, store, ["think", "off"])
        assert state.think == "none"

    def test_default_sets_none_field(self, store: Store) -> None:
        state = make_state(supports_thinking=True)
        cmd_set(state, store, ["think", "default"])
        assert state.think is None

    def test_explicit_level(self, store: Store) -> None:
        state = make_state(supports_thinking=True)
        cmd_set(state, store, ["think", "high"])
        assert state.think == "high"

    def test_invalid_level(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state(supports_thinking=True)
        cmd_set(state, store, ["think", "bogus"])
        assert "Usage:" in capsys.readouterr().out

    def test_rejects_when_unsupported(
        self, store: Store, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = make_state(supports_thinking=False)
        cmd_set(state, store, ["think", "high"])
        assert "not supported" in capsys.readouterr().out
        assert state.think == "none"  # unchanged default

    def test_off_allowed_even_when_unsupported(self, store: Store) -> None:
        state = make_state(supports_thinking=False)
        state.think = "medium"
        cmd_set(state, store, ["think", "off"])
        assert state.think == "none"


class TestSetVerbose:
    def test_on(self, store: Store) -> None:
        state = make_state()
        cmd_set(state, store, ["verbose", "on"])
        assert state.verbose is True

    def test_off(self, store: Store) -> None:
        state = make_state()
        state.verbose = True
        cmd_set(state, store, ["verbose", "off"])
        assert state.verbose is False

    def test_default_is_off(self, store: Store) -> None:
        assert make_state().verbose is False

    def test_show_current(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_set(make_state(), store, ["verbose"])
        assert "Verbose: off" in capsys.readouterr().out

    def test_invalid(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_set(make_state(), store, ["verbose", "maybe"])
        assert "Usage: /set verbose" in capsys.readouterr().out


class TestSetParameter:
    def test_set_float(self, store: Store) -> None:
        state = make_state()
        cmd_set(state, store, ["parameter", "temperature", "0.7"])
        assert state.params["temperature"] == 0.7

    def test_set_int(self, store: Store) -> None:
        state = make_state()
        cmd_set(state, store, ["parameter", "max_tokens", "256"])
        assert state.params["max_tokens"] == 256

    def test_unknown_parameter(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state()
        cmd_set(state, store, ["parameter", "nonsense", "1"])
        assert "Unknown parameter" in capsys.readouterr().out

    def test_bad_value(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state()
        cmd_set(state, store, ["parameter", "temperature", "hot"])
        assert "Could not parse" in capsys.readouterr().out

    def test_clear_set_parameter(self, store: Store) -> None:
        state = make_state()
        state.params["seed"] = 42
        cmd_set(state, store, ["parameter", "seed"])
        assert "seed" not in state.params

    def test_clear_unset_parameter(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state()
        cmd_set(state, store, ["parameter", "seed"])
        assert "not set" in capsys.readouterr().out


class TestSetDispatch:
    def test_no_args_usage(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_set(make_state(), store, [])
        assert "Usage:" in capsys.readouterr().out

    def test_unknown_subcommand(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_set(make_state(), store, ["frobnicate"])
        assert "Unknown subcommand" in capsys.readouterr().out


class TestDispatch:
    def test_non_slash_returns_false(self, store: Store) -> None:
        assert dispatch("hello there", make_state(), store) is False

    def test_known_command_runs(self, store: Store) -> None:
        state = make_state(messages=[Message("user", "u")])
        assert dispatch("/clear", state, store) is True
        assert state.messages == []

    def test_unknown_command(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        assert dispatch("/nope", make_state(), store) is True
        assert "Unknown command" in capsys.readouterr().out

    def test_bye_sets_quit(self, store: Store) -> None:
        state = make_state()
        cmd_bye(state, store, [])
        assert state.quit is True

    def test_help_prints_commands(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_help(make_state(), store, [])
        assert "Commands:" in capsys.readouterr().out

    def test_print_empty(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_print(make_state(), store, [])
        assert "(empty conversation)" in capsys.readouterr().out


class _FakeChatClient:
    def __init__(self, chunks) -> None:
        self._chunks = chunks

    def chat_stream(self, model, messages, params, think=None, timeout=600.0) -> Iterator:
        yield from self._chunks


class TestRunInference:
    def test_appends_assistant_and_persists(
        self, store: Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chunks = [ContentDelta("Hel"), ContentDelta("lo"), FinalStats(None, 2, 0.5)]
        monkeypatch.setattr(inference, "client_for", lambda _p: _FakeChatClient(chunks))
        state = make_state(messages=[Message("user", "hi")])
        persist(state, store)
        run_inference(state, store)
        assert state.messages[-1] == Message("assistant", "Hello")
        assert store.load_conversation(state.conv_id)[-1].content == "Hello"

    def test_stats_line_hidden_by_default(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        chunks = [ContentDelta("hi"), FinalStats(None, 2, 0.5)]
        monkeypatch.setattr(inference, "client_for", lambda _p: _FakeChatClient(chunks))
        run_inference(make_state(messages=[Message("user", "q")]), store)
        assert "[ total" not in capsys.readouterr().out  # verbose off by default

    def test_stats_line_shown_when_verbose(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        chunks = [ContentDelta("hi"), FinalStats(None, 2, 0.5)]
        monkeypatch.setattr(inference, "client_for", lambda _p: _FakeChatClient(chunks))
        state = make_state(messages=[Message("user", "q")])
        state.verbose = True
        run_inference(state, store)
        assert "[ total" in capsys.readouterr().out

    def test_network_error_reports_and_skips_append(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom(_p):
            class C:
                def chat_stream(self, *a, **k):
                    raise httpx.ConnectError("down")
                    yield  # make it a generator

            return C()

        monkeypatch.setattr(inference, "client_for", boom)
        state = make_state(messages=[Message("user", "hi")])
        run_inference(state, store)
        assert "could not reach" in capsys.readouterr().out
        assert state.messages == [Message("user", "hi")]  # no assistant appended

    def test_generic_error_reported(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom(_p):
            class C:
                def chat_stream(self, *a, **k):
                    raise ValueError("weird")
                    yield

            return C()

        monkeypatch.setattr(inference, "client_for", boom)
        state = make_state(messages=[Message("user", "hi")])
        run_inference(state, store)
        assert "[error:" in capsys.readouterr().out


class TestCopy:
    def test_copies_last_reply(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        copied: dict = {}

        def fake_copy(text: str) -> str:
            copied["text"] = text
            return "pbcopy"

        monkeypatch.setattr(commands.clipboard, "copy", fake_copy)
        state = make_state(messages=[Message("user", "q"), Message("assistant", "the answer")])
        cmd_copy(state, store, [])
        assert copied["text"] == "the answer"
        assert "Copied last reply to clipboard" in capsys.readouterr().out

    def test_copy_all_sends_transcript(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        copied: dict = {}
        monkeypatch.setattr(
            commands.clipboard, "copy", lambda t: (copied.update(text=t), "pbcopy")[1]
        )
        state = make_state(messages=[Message("user", "q"), Message("assistant", "a")])
        cmd_copy(state, store, ["all"])
        assert "## user" in copied["text"] and "## assistant" in copied["text"]
        assert "Copied conversation" in capsys.readouterr().out

    def test_osc52_is_noted(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(commands.clipboard, "copy", lambda t: "osc52")
        state = make_state(messages=[Message("user", "q"), Message("assistant", "a")])
        cmd_copy(state, store, [])
        assert "via OSC 52" in capsys.readouterr().out

    def test_nothing_to_copy(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_copy(make_state(), store, [])
        assert "Nothing to copy" in capsys.readouterr().out

    def test_no_assistant_reply_yet(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_copy(make_state(messages=[Message("user", "q")]), store, [])
        assert "no assistant reply yet" in capsys.readouterr().out

    def test_usage_on_bad_arg(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state(messages=[Message("user", "q"), Message("assistant", "a")])
        cmd_copy(state, store, ["nonsense"])
        assert "Usage: /copy" in capsys.readouterr().out


class TestSave:
    def test_writes_markdown_transcript(
        self, store: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = make_state(messages=[Message("user", "hello"), Message("assistant", "hi back")])
        path = tmp_path / "chat.md"
        cmd_save(state, store, [str(path)])
        content = path.read_text()
        assert "## user" in content and "hello" in content
        assert "## assistant" in content and "hi back" in content
        assert state.full_model in content
        assert "Saved conversation to" in capsys.readouterr().out

    def test_usage_without_path(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        state = make_state(messages=[Message("user", "q"), Message("assistant", "a")])
        cmd_save(state, store, [])
        assert "Usage: /save" in capsys.readouterr().out

    def test_nothing_to_save(
        self, store: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cmd_save(make_state(), store, [str(tmp_path / "x.md")])
        assert "Nothing to save yet" in capsys.readouterr().out
        assert not (tmp_path / "x.md").exists()

    def test_refuses_to_overwrite(
        self, store: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "exists.md"
        path.write_text("original")
        state = make_state(messages=[Message("user", "q"), Message("assistant", "a")])
        cmd_save(state, store, [str(path)])
        assert "already exists" in capsys.readouterr().out
        assert path.read_text() == "original"  # not clobbered

    def test_write_error_reported(
        self, store: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = make_state(messages=[Message("user", "q"), Message("assistant", "a")])
        cmd_save(state, store, [str(tmp_path / "missing-dir" / "deep.md")])
        assert "Could not write" in capsys.readouterr().out


class TestApplySettings:
    def test_inserts_system_prompt(self, store: Store) -> None:
        st = make_state()
        apply_settings(st, Settings(system="Be brief."))
        assert st.messages[0] == Message("system", "Be brief.")

    def test_think_default_maps_to_none(self, store: Store) -> None:
        st = make_state(supports_thinking=True)
        apply_settings(st, Settings(think="default"))
        assert st.think is None

    def test_think_level(self, store: Store) -> None:
        st = make_state(supports_thinking=True)
        apply_settings(st, Settings(think="high"))
        assert st.think == "high"

    def test_invalid_think_ignored(self, store: Store) -> None:
        st = make_state()  # built-in default is "none"
        apply_settings(st, Settings(think="bogus"))
        assert st.think == "none"

    def test_params_coerced_and_unknown_dropped(self, store: Store) -> None:
        st = make_state()
        apply_settings(st, Settings(parameters={"temperature": 1, "nonsense": "x"}))
        assert st.params == {"temperature": 1.0}  # int→float, unknown key dropped

    def test_empty_settings_is_noop(self, store: Store) -> None:
        st = make_state()
        apply_settings(st, Settings())
        assert st.messages == [] and st.think == "none" and st.params == {}


class TestRemember:
    def test_writes_current_settings(self, store: Store) -> None:
        st = make_state(messages=[Message("system", "Be terse."), Message("user", "q")])
        st.think = "high"
        st.params["temperature"] = 0.5
        cmd_remember(st, store, [])
        md = config._read_model_defaults()
        assert md[st.model].system == "Be terse."
        assert md[st.model].think == "high"
        assert md[st.model].parameters == {"temperature": 0.5}

    def test_verbose_is_not_remembered_per_model(self, store: Store) -> None:
        # verbose is a session-wide UI preference ([defaults].verbose), not a
        # model setting — /remember must not pin it.
        st = make_state()
        st.verbose = True
        cmd_remember(st, store, [])
        assert "verbose" not in config.MODEL_DEFAULTS_PATH.read_text()

    def test_think_off_stored_as_default(self, store: Store) -> None:
        st = make_state()
        st.think = None
        cmd_remember(st, store, [])
        assert config._read_model_defaults()[st.model].think == "default"

    def test_confirmation_printed(self, store: Store, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_remember(make_state(), store, [])
        assert "Remembered defaults for" in capsys.readouterr().out


class TestRunOneshot:
    def test_streams_plain_output_and_persists(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        chunks = [ContentDelta("Hel"), ContentDelta("lo"), FinalStats(None, 2, 0.1)]
        monkeypatch.setattr(inference, "client_for", lambda _p: _FakeChatClient(chunks))
        state = make_state()
        run_oneshot(state, store, "hi")
        out = capsys.readouterr().out
        assert out == "Hello\n"  # plain content + trailing newline; no stats/spinner
        assert state.messages == [Message("user", "hi"), Message("assistant", "Hello")]
        assert store.load_conversation(state.conv_id)[-1].content == "Hello"

    def test_already_newline_terminated_not_doubled(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            inference, "client_for", lambda _p: _FakeChatClient([ContentDelta("done\n")])
        )
        run_oneshot(make_state(), store, "hi")
        assert capsys.readouterr().out == "done\n"

    def test_network_error_to_stderr_keeps_stdout_clean(
        self, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom(_p):
            class C:
                def chat_stream(self, *a, **k):
                    raise httpx.ConnectError("down")
                    yield

            return C()

        monkeypatch.setattr(inference, "client_for", boom)
        state = make_state()
        run_oneshot(state, store, "hi")
        captured = capsys.readouterr()
        assert captured.out == ""  # stdout stays clean for pipelines
        assert "could not reach" in captured.err
        assert state.messages == [Message("user", "hi")]  # no assistant persisted
