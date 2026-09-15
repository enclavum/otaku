"""The transport's pure edges: the integer reader every engine's JSON
goes through, the purpose rule, and how a failure is filed."""

import contextlib
import socket
import threading
import time
from pathlib import Path

import pytest

from otaku.providers import UnreachableError
from otaku.providers.http import Cut, Http, _Cutting, positive_int


class TestPositiveInt:
    @pytest.mark.parametrize("value", [1, 4096, 10**12])
    def test_a_positive_int_is_itself(self, value: int) -> None:
        assert positive_int(value) == value

    @pytest.mark.parametrize("value", [0, -1, None, "4096", 4096.0, [4096], {}])
    def test_anything_else_is_none(self, value: object) -> None:
        assert positive_int(value) is None

    def test_a_bool_is_never_a_count(self) -> None:
        # True is an int to Python; a flag read as a count would be 1.
        assert positive_int(True) is None
        assert positive_int(False) is None


class _Sink:
    def __init__(self) -> None:
        self.filed: list[tuple[str, BaseException]] = []

    def record(self, context: str, exc: BaseException) -> Path:
        self.filed.append((context, exc))
        return Path("/dev/null")


class TestPurposes:
    def test_a_loud_call_needs_a_purpose_before_anything_is_sent(self) -> None:
        raw = Http("engine", {})
        with pytest.raises(ValueError):
            raw.get("http://127.0.0.1:9/v1/models")

    def test_a_views_purpose_serves_its_calls(self) -> None:
        # The view carries the word; the call needs none of its own.
        # Nothing listens on the port, so the answer is the family's.
        view = Http("engine", {}).within(0.5, "listing")
        with pytest.raises(UnreachableError):
            view.get("http://127.0.0.1:9/v1/models")

    def test_a_quiet_call_needs_no_purpose_and_answers_none(self) -> None:
        raw = Http("engine", {})
        assert raw.get("http://127.0.0.1:9/v1/models", quiet=True) is None

    def test_a_view_of_a_view_is_still_the_transport(self) -> None:
        inner = Http("engine", {}).within(1.0, "listing").within(0.5, "model")
        assert isinstance(inner, Http)


class TestFiling:
    def test_a_failure_is_filed_under_the_provider_and_the_purpose(self) -> None:
        sink = _Sink()
        failure = UnreachableError("Could not reach engine.")
        Http("engine", {}, sink).record(failure, "load")
        assert sink.filed == [("engine [load]", failure)]

    def test_a_views_purpose_stands_in_for_the_calls(self) -> None:
        sink = _Sink()
        failure = UnreachableError("Could not reach engine.")
        Http("engine", {}, sink).within(1.0, "listing").record(failure)
        assert sink.filed == [("engine [listing]", failure)]

    def test_filing_without_any_purpose_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Http("engine", {}, _Sink()).record(UnreachableError("Could not reach engine."))

    def test_without_a_sink_nothing_is_filed_and_nothing_breaks(self) -> None:
        Http("engine", {}).record(UnreachableError("Could not reach engine."), "load")


class TestCut:
    def test_a_cut_wakes_a_read_blocked_on_the_connection_it_armed(self) -> None:
        # A server that answers nothing, on a port of its own: the read
        # blocks the way a prefill's does, and only the cut ends it.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        cut = Cut()
        try:
            stream = _Cutting(cut).connect_tcp("127.0.0.1", listener.getsockname()[1], timeout=2.0)
            outcome: list[object] = []

            def read() -> None:
                try:
                    outcome.append(stream.read(1, timeout=5.0))
                except Exception as e:  # whatever the wake-up raises is the point
                    outcome.append(e)

            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            reader.join(0.2)
            assert reader.is_alive()  # blocked, as a prefill's read is
            cut.cut()
            reader.join(2.0)
            assert not reader.is_alive()
            assert cut.asked
        finally:
            listener.close()

    def test_a_cut_asked_before_the_connection_lands_when_it_connects(self) -> None:
        # The shut-down socket ends the read at once, as a closed peer
        # would: nothing to wait for.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        cut = Cut()
        cut.cut()
        try:
            stream = _Cutting(cut).connect_tcp("127.0.0.1", listener.getsockname()[1], timeout=2.0)
            started = time.monotonic()
            with contextlib.suppress(Exception):  # a refused read ends it just the same
                assert stream.read(1, timeout=2.0) == b""
            assert time.monotonic() - started < 1.0
        finally:
            listener.close()
