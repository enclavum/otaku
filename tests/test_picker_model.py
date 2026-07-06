"""Tests for ModelPicker logic and provider collection."""

from __future__ import annotations

import time

import pytest

from otaku.pickers import model as model_mod
from otaku.pickers.model import ModelEntry, ModelPicker, _collect, pick_model
from tests.support import FakeClient, make_provider

PROVIDERS = {"ollama": make_provider("ollama"), "lmstudio": make_provider("lmstudio")}


def _entries() -> list[ModelEntry]:
    return [
        ModelEntry("ollama/llama3", "ollama", "llama3", loaded=True, size_bytes=1000),
        ModelEntry("ollama/qwen", "ollama", "qwen", loaded=False, size_bytes=2000),
        ModelEntry("lmstudio/phi", "lmstudio", "phi", loaded=False, size_bytes=None),
    ]


class TestInit:
    def test_initial_spec_positions_cursor(self) -> None:
        p = ModelPicker(PROVIDERS, _entries(), initial_spec="ollama/qwen")
        assert p.cursor == 1

    def test_unknown_initial_spec_defaults_to_top(self) -> None:
        p = ModelPicker(PROVIDERS, _entries(), initial_spec="nope/nope")
        assert p.cursor == 0


class TestFilter:
    def test_filter_by_spec(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p.query = "qwen"
        p._refilter()
        assert [e.model for e in p.filtered] == ["qwen"]

    def test_empty_query_shows_all(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p.query = ""
        p._refilter()
        assert len(p.filtered) == 3

    def test_refilter_clamps_cursor(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p.cursor = 2
        p.query = "qwen"
        p._refilter()
        assert p.cursor == 0


class TestCursor:
    def test_move_clamped(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p._move_cursor(-1)
        assert p.cursor == 0
        p._move_cursor(99)
        assert p.cursor == 2


class TestConfirmFlow:
    def test_request_load_on_unloaded(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p.cursor = 1  # qwen, not loaded
        p._request_load()
        assert p.confirming_action == "load"
        assert p.confirming_entry is not None and p.confirming_entry.model == "qwen"

    def test_request_load_noop_on_loaded(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p.cursor = 0  # llama3, loaded
        p._request_load()
        assert p.confirming_action is None

    def test_request_unload_on_loaded(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p.cursor = 0
        p._request_unload()
        assert p.confirming_action == "unload"

    def test_request_unload_noop_on_unloaded(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p.cursor = 1
        p._request_unload()
        assert p.confirming_action is None

    def test_request_load_noop_while_filtering(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p.in_filter = True
        p.cursor = 1
        p._request_load()
        assert p.confirming_action is None

    def test_confirm_yes_starts_action(self, monkeypatch: pytest.MonkeyPatch) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        recorded: list[tuple] = []
        monkeypatch.setattr(
            p,
            "_start_action",
            lambda entry, *, load, exit_on_success: recorded.append(
                (entry.model, load, exit_on_success)
            ),
        )
        p.cursor = 1
        p._request_load()
        p._confirm_yes()
        assert recorded == [("qwen", True, False)]
        assert p.confirming_action is None

    def test_confirm_no_clears(self) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        p.cursor = 1
        p._request_load()
        p._confirm_no()
        assert p.confirming_action is None
        assert p.confirming_entry is None


class TestOnEnter:
    def test_loaded_model_selects_and_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        monkeypatch.setattr(p.app, "exit", lambda: None)
        p.cursor = 0  # loaded
        p._on_enter()
        assert p.result == "ollama/llama3"

    def test_unloaded_model_triggers_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        p = ModelPicker(PROVIDERS, _entries())
        recorded: list[tuple] = []
        monkeypatch.setattr(
            p,
            "_start_action",
            lambda entry, *, load, exit_on_success: recorded.append(
                (entry.model, load, exit_on_success)
            ),
        )
        p.cursor = 1  # not loaded
        p._on_enter()
        assert recorded == [("qwen", True, True)]


class TestStartAction:
    def test_load_updates_state_via_real_threads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov = make_provider("ollama")
        fake = FakeClient(prov, models=["qwen"], loaded=set())
        monkeypatch.setattr(model_mod, "client_for", lambda _p: fake)
        entry = ModelEntry("ollama/qwen", "ollama", "qwen", loaded=False)
        p = ModelPicker({"ollama": prov}, [entry])
        p._start_action(entry, load=True, exit_on_success=False)

        deadline = time.time() + 3.0
        while p.busy and time.time() < deadline:
            time.sleep(0.02)
        assert p.busy is False
        assert p.busy_error is None
        assert entry.loaded is True  # refreshed from fake.loaded_models()

    def test_action_error_is_captured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov = make_provider("ollama")
        fake = FakeClient(prov, unload_error=RuntimeError("nope"))
        monkeypatch.setattr(model_mod, "client_for", lambda _p: fake)
        entry = ModelEntry("ollama/qwen", "ollama", "qwen", loaded=True)
        p = ModelPicker({"ollama": prov}, [entry])
        p._start_action(entry, load=False, exit_on_success=False)

        deadline = time.time() + 3.0
        while p.busy and time.time() < deadline:
            time.sleep(0.02)
        assert p.busy is False
        assert p.busy_error is not None and "nope" in p.busy_error


class TestCollectEntries:
    def test_gathers_across_providers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_client_for(provider):
            if provider.name == "ollama":
                return FakeClient(
                    provider, models=["llama3"], loaded={"llama3"}, sizes={"llama3": 500}
                )
            return FakeClient(provider, models=["phi"], loaded=set())

        monkeypatch.setattr(model_mod, "client_for", fake_client_for)
        entries = _collect(PROVIDERS)[0]
        specs = {e.full_spec for e in entries}
        assert specs == {"ollama/llama3", "lmstudio/phi"}
        llama = next(e for e in entries if e.model == "llama3")
        assert llama.loaded is True
        assert llama.size_bytes == 500

    def test_provider_error_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_client_for(provider):
            if provider.name == "ollama":
                return FakeClient(provider, list_error=RuntimeError("down"))
            return FakeClient(provider, models=["phi"])

        monkeypatch.setattr(model_mod, "client_for", fake_client_for)
        entries = _collect(PROVIDERS)[0]
        assert {e.full_spec for e in entries} == {"lmstudio/phi"}


class TestPickModel:
    def test_returns_none_and_warns_when_no_models(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(model_mod, "_collect", lambda _p: ([], set()))
        assert pick_model(PROVIDERS) is None
        assert "No models reachable" in capsys.readouterr().out
