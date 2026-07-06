"""Tests for HistoryPicker state-machine logic and helpers.

The full prompt_toolkit Application is never run; the picker's behaviour
methods are driven directly against a real (encrypted) store.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from otaku.pickers import history as history_mod
from otaku.pickers.history import HistoryPicker, _human_age, _list_label, _wrap, pick_history
from otaku.storage.store import Conversation, Message, Store


class _DummyApp:
    """Stand-in for the running Application: only exit() is exercised by the
    picker's behaviour methods outside a real event loop."""

    def exit(self) -> None:
        pass


def _seed(store: Store) -> list[Conversation]:
    a = store.create_conversation("ollama/llama3")
    store.snapshot_messages(a, [Message("user", "python question"), Message("assistant", "answer")])
    store.update_summary(a, "a chat about python")
    b = store.create_conversation("lmstudio/qwen")
    store.snapshot_messages(b, [Message("user", "rust question"), Message("assistant", "answer")])
    store.update_summary(b, "a chat about rust")
    return store.list_conversations()


class TestInit:
    def test_initial_id_positions_cursor(self, store: Store) -> None:
        convs = _seed(store)
        target = convs[1]
        picker = HistoryPicker(store, convs, initial_id=target.id)
        assert picker.list_cursor == 1

    def test_no_initial_id_starts_at_top(self, store: Store) -> None:
        convs = _seed(store)
        assert HistoryPicker(store, convs).list_cursor == 0


class TestFilter:
    def test_filter_matches_summary(self, store: Store) -> None:
        convs = _seed(store)
        picker = HistoryPicker(store, convs)
        picker.query = "rust"
        picker._refilter()
        assert len(picker.filtered) == 1
        assert "rust" in picker.filtered[0].summary

    def test_filter_matches_model(self, store: Store) -> None:
        convs = _seed(store)
        picker = HistoryPicker(store, convs)
        picker.query = "ollama"
        picker._refilter()
        assert all("ollama" in c.model for c in picker.filtered)

    def test_filter_matches_full_content(self, store: Store) -> None:
        # a term that appears only mid-conversation (not summary/first/model)
        cid = store.create_conversation("ollama/llama3")
        store.snapshot_messages(
            cid,
            [
                Message("user", "hello"),
                Message("assistant", "the SECRET recipe"),
                Message("user", "bye"),
            ],
        )
        convs = store.list_conversations(limit=None)
        picker = HistoryPicker(store, convs)
        picker.query = "secret"
        picker._refilter()
        assert any(c.id == cid for c in picker.filtered)

    def test_empty_query_shows_all(self, store: Store) -> None:
        convs = _seed(store)
        picker = HistoryPicker(store, convs)
        picker.query = ""
        picker._refilter()
        assert len(picker.filtered) == len(convs)

    def test_filter_clamps_cursor(self, store: Store) -> None:
        convs = _seed(store)
        picker = HistoryPicker(store, convs)
        picker.list_cursor = 1
        picker.query = "rust"  # narrows to 1 item
        picker._refilter()
        assert picker.list_cursor == 0


class TestCursor:
    def test_move_within_bounds(self, store: Store) -> None:
        picker = HistoryPicker(store, _seed(store))
        picker._move_cursor(1)
        assert picker.list_cursor == 1

    def test_move_clamped_at_top(self, store: Store) -> None:
        picker = HistoryPicker(store, _seed(store))
        picker._move_cursor(-5)
        assert picker.list_cursor == 0

    def test_move_clamped_at_bottom(self, store: Store) -> None:
        convs = _seed(store)
        picker = HistoryPicker(store, convs)
        picker._move_cursor(99)
        assert picker.list_cursor == len(convs) - 1


class TestDrillAndResume:
    def test_enter_loads_turns(self, store: Store) -> None:
        convs = _seed(store)
        picker = HistoryPicker(store, convs)
        picker.list_cursor = 0
        picker._enter()
        assert picker.in_turns is True
        assert len(picker.loaded_msgs) == 2
        assert picker.turn_cursor == 1  # last turn

    def test_enter_in_turns_sets_result_truncated(
        self, store: Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(history_mod, "get_app", lambda: _DummyApp())
        convs = _seed(store)
        picker = HistoryPicker(store, convs)
        picker._enter()  # into turns
        picker.turn_cursor = 0  # resume from first turn
        picker._enter()  # confirm
        assert picker.result is not None
        _conv_id, truncated, total = picker.result
        assert len(truncated) == 1
        assert total == 2

    def test_escape_from_turns_returns_to_list(self, store: Store) -> None:
        convs = _seed(store)
        picker = HistoryPicker(store, convs)
        picker._enter()
        assert picker.in_turns is True
        picker._escape()
        assert picker.in_turns is False
        assert picker.loaded_msgs == []

    def test_escape_clears_filter(self, store: Store) -> None:
        picker = HistoryPicker(store, _seed(store))
        picker.in_filter = True
        picker.query = "rust"
        picker._refilter()
        picker._escape()
        assert picker.in_filter is False
        assert picker.query == ""
        assert len(picker.filtered) == 2


class TestDelete:
    def test_request_and_confirm_delete(self, store: Store) -> None:
        convs = _seed(store)
        picker = HistoryPicker(store, convs)
        picker.list_cursor = 0
        target = picker.filtered[0].id
        picker._request_delete()
        assert picker.confirming_delete is True
        picker._do_delete()
        assert picker.confirming_delete is False
        assert all(c.id != target for c in picker.all)
        assert store.list_conversations()[0].id != target or len(store.list_conversations()) == 1

    def test_request_delete_noop_in_readonly(self, ro_store: Store) -> None:
        # seed via a writable store on the same file is unnecessary; readonly
        # picker just must refuse to enter the confirm state.
        conv = Conversation(id=uuid.uuid4(), model="m", updated_at=datetime.now(UTC), num_turns=1)
        picker = HistoryPicker(ro_store, [conv])
        picker._request_delete()
        assert picker.confirming_delete is False


class TestRun:
    def test_run_returns_none_when_empty(self, store: Store) -> None:
        assert HistoryPicker(store, []).run() is None

    def test_pick_history_empty_store(self, store: Store) -> None:
        assert pick_history(store) is None


def _conv(title: str = "", summary: str = "", first_user: str = "") -> Conversation:
    return Conversation(
        id=uuid.uuid4(),
        model="m",
        updated_at=datetime.now(UTC),
        title=title,
        summary=summary,
        first_user=first_user,
    )


class TestListLabel:
    def test_title_and_summary(self) -> None:
        assert _list_label(_conv(title="T", summary="S")) == "T / S"

    def test_title_only(self) -> None:
        assert _list_label(_conv(title="T")) == "T"

    def test_summary_only(self) -> None:
        assert _list_label(_conv(summary="S")) == "S"

    def test_placeholder_when_neither(self) -> None:
        # first_user is not used as a fallback here — placeholder when no
        # title and no summary
        assert _list_label(_conv(first_user="a question")) == "(untitled)"


class TestTitleRendering:
    def test_items_row_shows_title(self, store: Store) -> None:
        cid = store.create_conversation("ollama/llama3")
        store.snapshot_messages(cid, [Message("user", "q")])
        store.update_title(cid, "MyTitle")
        picker = HistoryPicker(store, store.list_conversations())
        text = "".join(t for _, t in picker._items_text())
        assert "MyTitle" in text

    def test_items_row_placeholder(self, store: Store) -> None:
        cid = store.create_conversation("ollama/llama3")
        store.snapshot_messages(cid, [Message("user", "hello")])  # no title, no summary
        picker = HistoryPicker(store, store.list_conversations())
        text = "".join(t for _, t in picker._items_text())
        assert "(untitled)" in text

    def test_preview_title_before_summary(self, store: Store) -> None:
        cid = store.create_conversation("ollama/llama3")
        store.snapshot_messages(cid, [Message("user", "q"), Message("assistant", "a")])
        store.update_summary(cid, "the summary text")
        store.update_title(cid, "the title text")
        picker = HistoryPicker(store, store.list_conversations())
        picker.list_cursor = 0
        text = "".join(t for _, t in picker._preview_text())
        assert text.index("the title text") < text.index("the summary text")


class TestHelpers:
    def test_human_age_just_now(self) -> None:
        assert _human_age(datetime.now(UTC)) == "just now"

    def test_human_age_minutes(self) -> None:
        assert _human_age(datetime.now(UTC) - timedelta(minutes=5)) == "5m ago"

    def test_human_age_hours(self) -> None:
        assert _human_age(datetime.now(UTC) - timedelta(hours=3)) == "3h ago"

    def test_human_age_days(self) -> None:
        assert _human_age(datetime.now(UTC) - timedelta(days=2)) == "2d ago"

    def test_wrap_splits_long_line(self) -> None:
        out = _wrap("aaaa bbbb cccc", 6)
        assert all(len(line) <= 6 for line in out)

    def test_wrap_preserves_blank_lines(self) -> None:
        assert _wrap("a\n\nb", 10) == ["a", "", "b"]

    def test_wrap_zero_width_returns_input(self) -> None:
        assert _wrap("hello", 0) == ["hello"]
