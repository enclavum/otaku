"""The transport's pure edges: the integer reader every engine's JSON
goes through, the purpose rule, and how a failure is filed."""

import contextlib
import datetime
import email.utils
import socket
import threading
import time
from pathlib import Path

import pytest

from otaku.providers import UnreachableError
from otaku.providers.http import (
    Cut,
    Http,
    _Cutting,
    explanation,
    positive_int,
    retry_after,
    ticking,
)


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


class TestTicking:
    def test_the_answer_comes_back_and_the_hook_ticks_meanwhile(self) -> None:
        ticks = 0

        def idle() -> None:
            nonlocal ticks
            ticks += 1

        def call() -> int:
            time.sleep(0.15)
            return 7

        assert ticking(call, idle, Cut()) == 7
        assert ticks > 0

    def test_what_the_call_raises_is_raised(self) -> None:
        def call() -> None:
            raise ValueError("no")

        with pytest.raises(ValueError):
            ticking(call, lambda: None, Cut())

    def test_a_hook_saying_nobody_waits_asks_the_cut(self) -> None:
        cut = Cut()

        def call() -> str:
            time.sleep(0.2)
            return "done"

        assert ticking(call, lambda: False, cut) == "done"
        assert cut.asked


class TestExplanation:
    def test_the_error_objects_message_out_of_its_envelope(self) -> None:
        assert explanation({"error": {"message": "no such model", "code": 404}}) == "no such model"
        assert explanation({"error": "plain words"}) == "plain words"

    def test_openrouters_metadata_rides_along(self) -> None:
        # The upstream's own words and which provider they came from:
        # the half a reader can act on.
        body = {
            "error": {
                "code": 429,
                "message": "Provider returned error",
                "metadata": {
                    "raw": "temporarily rate-limited upstream",
                    "provider_name": "DeepInfra",
                },
            }
        }
        assert (
            explanation(body)
            == "Provider returned error: temporarily rate-limited upstream (DeepInfra)"
        )

    def test_a_moderations_reasons_and_passage_ride_along(self) -> None:
        body = {
            "error": {
                "code": 403,
                "message": "Input flagged",
                "metadata": {
                    "reasons": ["violence"],
                    "flagged_input": "the knife",
                    "provider_name": "OpenAI",
                },
            }
        }
        assert explanation(body) == "Input flagged (OpenAI) — violence: 'the knife'"

    def test_koboldcpps_detail_is_unwrapped(self) -> None:
        busy = {
            "detail": {
                "msg": "Server is busy; please try again later.",
                "type": "service_unavailable",
            }
        }
        assert explanation(busy) == "Server is busy; please try again later."
        assert explanation({"detail": "no such thing"}) == "no such thing"

    def test_no_envelope_is_the_text_as_it_came(self) -> None:
        assert explanation(None, "<html>gateway</html>") == "<html>gateway</html>"
        assert explanation({"unrelated": 1}, "text") == "text"
        assert explanation({"error": {}}, "") == ""


class TestRetryAfter:
    def test_a_count_of_seconds(self) -> None:
        assert retry_after("20") == 20.0
        assert retry_after(" 1.5 ") == 1.5
        assert retry_after("-3") == 0.0

    def test_an_http_date_is_the_seconds_until_it(self) -> None:
        soon = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=90)
        wait = retry_after(email.utils.format_datetime(soon, usegmt=True))
        assert wait is not None and 85 <= wait <= 90
        passed = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=90)
        assert retry_after(email.utils.format_datetime(passed, usegmt=True)) == 0.0

    def test_nothing_or_nonsense_is_none(self) -> None:
        assert retry_after(None) is None
        assert retry_after("") is None
        assert retry_after("soon") is None
