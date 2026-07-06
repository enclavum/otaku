"""Tests for background conversation summaries: generate_summary + SummaryWorker."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from otaku.chat import summary
from otaku.chat.summary import SUMMARY_PROMPT, SummaryWorker, generate_summary
from otaku.client import ContentDelta
from otaku.storage.crypto import Cipher
from otaku.storage.store import Message, Store
from tests.support import make_provider

PROVIDER = make_provider()


class _FakeStreamClient:
    """A client whose chat_stream yields canned ContentDelta chunks (or raises),
    and records the messages it was handed. `closed` flips when the generator is
    closed (proving cancellation tears the stream down)."""

    def __init__(self, texts: list[str] | None = None, error: Exception | None = None) -> None:
        self._texts = texts or []
        self._error = error
        self.seen_messages: list[Message] = []
        self.closed = False

    def chat_stream(
        self, model, messages, params, think=None, timeout=600.0
    ) -> Iterator[ContentDelta]:
        self.seen_messages = list(messages)
        if self._error is not None:
            raise self._error
        try:
            for t in self._texts:
                yield ContentDelta(t)
        finally:
            self.closed = True


def _needs_summary_conv(store: Store) -> tuple:
    cid = store.create_conversation("m")
    msgs = [Message("user", "hello"), Message("assistant", "hi there")]
    store.snapshot_messages(cid, msgs)
    return cid, msgs


# ---------- generate_summary ----------


def test_returns_none_for_empty_messages(store: Store) -> None:
    cid = store.create_conversation("m")
    assert generate_summary(store, PROVIDER, "m", cid, []) is None


def test_returns_none_when_not_needed(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    cid, msgs = _needs_summary_conv(store)
    monkeypatch.setattr(store, "needs_summary", lambda _cid: False)
    called = _FakeStreamClient(["nope"])
    monkeypatch.setattr(summary, "client_for", lambda _p: called)
    assert generate_summary(store, PROVIDER, "m", cid, msgs) is None
    assert called.seen_messages == []  # never invoked the model


def test_success_persists_and_returns(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    cid, msgs = _needs_summary_conv(store)
    fake = _FakeStreamClient(["A short ", "summary."])
    monkeypatch.setattr(summary, "client_for", lambda _p: fake)
    result = generate_summary(store, PROVIDER, "m", cid, msgs)
    assert result == "A short summary."
    assert store.list_conversations()[0].summary == "A short summary."


def test_appends_summary_prompt_as_final_user_turn(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, msgs = _needs_summary_conv(store)
    fake = _FakeStreamClient(["x"])
    monkeypatch.setattr(summary, "client_for", lambda _p: fake)
    generate_summary(store, PROVIDER, "m", cid, msgs)
    assert fake.seen_messages[-1] == Message("user", SUMMARY_PROMPT)
    assert fake.seen_messages[:-1] == msgs


def test_empty_output_returns_none(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    cid, msgs = _needs_summary_conv(store)
    monkeypatch.setattr(summary, "client_for", lambda _p: _FakeStreamClient(["   "]))
    assert generate_summary(store, PROVIDER, "m", cid, msgs) is None
    assert store.list_conversations()[0].summary == ""


def test_stream_error_returns_none(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    cid, msgs = _needs_summary_conv(store)
    monkeypatch.setattr(
        summary, "client_for", lambda _p: _FakeStreamClient(error=RuntimeError("boom"))
    )
    assert generate_summary(store, PROVIDER, "m", cid, msgs) is None


def test_precancelled_never_calls_model(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    cid, msgs = _needs_summary_conv(store)
    fake = _FakeStreamClient(["nope"])
    monkeypatch.setattr(summary, "client_for", lambda _p: fake)
    cancel = threading.Event()
    cancel.set()
    assert generate_summary(store, PROVIDER, "m", cid, msgs, cancel) is None
    assert fake.seen_messages == []  # bailed before touching the model
    assert store.list_conversations()[0].summary == ""


def test_cancel_midstream_discards_and_closes(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, msgs = _needs_summary_conv(store)
    cancel = threading.Event()

    class _CancelAfterFirst:
        def __init__(self) -> None:
            self.closed = False

        def chat_stream(self, *a, **k) -> Iterator[ContentDelta]:
            try:
                yield ContentDelta("one")
                cancel.set()  # user submits mid-stream
                yield ContentDelta("two")
            finally:
                self.closed = True

    fake = _CancelAfterFirst()
    monkeypatch.setattr(summary, "client_for", lambda _p: fake)
    assert generate_summary(store, PROVIDER, "m", cid, msgs, cancel) is None
    assert fake.closed is True  # stream torn down → server stops generating
    assert store.list_conversations()[0].summary == ""  # nothing persisted


# ---------- SummaryWorker ----------


def _factory(tmp_path: Path, cipher: Cipher) -> Callable[[], Store]:
    db_url = f"sqlite:///{tmp_path / 'store.db'}"  # same file the `store` fixture uses
    return lambda: Store.open(db_url, cipher)


def _wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_worker_summarizes_after_idle(
    store: Store, tmp_path: Path, cipher: Cipher, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, msgs = _needs_summary_conv(store)
    monkeypatch.setattr(summary, "client_for", lambda _p: _FakeStreamClient(["auto ", "summary"]))
    worker = SummaryWorker(store_factory=_factory(tmp_path, cipher), idle_seconds=0.05)
    worker.start()
    try:
        worker.schedule(PROVIDER, "m", cid, msgs)
        assert _wait_for(lambda: store.list_conversations()[0].summary == "auto summary")
    finally:
        worker.shutdown()


def test_worker_cancel_aborts_inflight(
    store: Store, tmp_path: Path, cipher: Cipher, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, msgs = _needs_summary_conv(store)
    started, release = threading.Event(), threading.Event()

    class _Blocking:
        def chat_stream(self, *a, **k) -> Iterator[ContentDelta]:
            started.set()
            yield ContentDelta("partial")
            release.wait(2)
            yield ContentDelta(" rest")

    monkeypatch.setattr(summary, "client_for", lambda _p: _Blocking())
    worker = SummaryWorker(store_factory=_factory(tmp_path, cipher), idle_seconds=0.01)
    worker.start()
    try:
        worker.schedule(PROVIDER, "m", cid, msgs)
        assert started.wait(2)  # generation began
        worker.cancel()  # user submits → abort the in-flight summary
        release.set()  # let the stream produce another chunk → cancel is seen
        time.sleep(0.2)
        assert store.list_conversations()[0].summary == ""  # never persisted
    finally:
        release.set()
        worker.shutdown()


def test_worker_shutdown_is_immediate_midflight(
    store: Store, tmp_path: Path, cipher: Cipher, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, msgs = _needs_summary_conv(store)
    started, release = threading.Event(), threading.Event()

    class _Blocking:
        def chat_stream(self, *a, **k) -> Iterator[ContentDelta]:
            started.set()
            release.wait(5)
            yield ContentDelta("x")

    monkeypatch.setattr(summary, "client_for", lambda _p: _Blocking())
    worker = SummaryWorker(store_factory=_factory(tmp_path, cipher), idle_seconds=0.01)
    worker.start()
    try:
        worker.schedule(PROVIDER, "m", cid, msgs)
        assert started.wait(2)  # a generation is blocked in-flight
        t0 = time.monotonic()
        worker.shutdown()  # must not wait for the blocked generation
        assert time.monotonic() - t0 < 0.5
    finally:
        release.set()


def test_worker_schedule_after_shutdown_is_noop(
    store: Store, tmp_path: Path, cipher: Cipher, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, msgs = _needs_summary_conv(store)
    monkeypatch.setattr(summary, "client_for", lambda _p: _FakeStreamClient(["nope"]))
    worker = SummaryWorker(store_factory=_factory(tmp_path, cipher), idle_seconds=0.01)
    worker.start()
    worker.shutdown()
    worker.schedule(PROVIDER, "m", cid, msgs)  # ignored — worker is stopped
    time.sleep(0.1)
    assert store.list_conversations()[0].summary == ""
