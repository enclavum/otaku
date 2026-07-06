"""Tests for the braille Spinner — mainly start/stop idempotence."""

from __future__ import annotations

from otaku.spinner import FRAMES, Spinner


def test_frames_nonempty() -> None:
    assert len(FRAMES) > 0


def test_stop_without_start_is_noop() -> None:
    Spinner().stop()  # must not raise


def test_start_is_idempotent() -> None:
    s = Spinner()
    assert s._thread is None
    s.start()
    try:
        first = s._thread
        assert first is not None
        s.start()  # second start must not spawn a new thread
        assert s._thread is first
    finally:
        s.stop()


def test_stop_clears_thread_and_is_repeatable() -> None:
    s = Spinner()
    s.start()
    s.stop()
    assert s._thread is None
    s.stop()  # repeat stop is a no-op


def test_label_is_stored() -> None:
    assert Spinner(label="Summarizing…")._label == "Summarizing…"
