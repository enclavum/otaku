"""A cut against a real connection: a read blocked the way a prefill's
is, ended by the cut armed at connect time — sockets and threads, so a
scenario rather than a unit test."""

import contextlib
import socket
import threading
import time

from otaku.providers.http import Cut, _Cutting


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
