"""The transport against real connections: a cut ending a read blocked
the way a prefill's is, armed at connect time; and a server on this
machine dialled as `localhost` — sockets and threads, so a scenario
rather than a unit test."""

import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpcore
import pytest

from otaku.providers.http import _LOOPBACK_CONNECT_TIMEOUT, Cut, Http, _Backend


class TestCut:
    def test_a_cut_wakes_a_read_blocked_on_the_connection_it_armed(self) -> None:
        # A server that answers nothing, on a port of its own: the read
        # blocks the way a prefill's does, and only the cut ends it.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        cut = Cut()
        try:
            stream = _Backend(cut).connect_tcp("127.0.0.1", listener.getsockname()[1], timeout=2.0)
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
            stream = _Backend(cut).connect_tcp("127.0.0.1", listener.getsockname()[1], timeout=2.0)
            started = time.monotonic()
            with contextlib.suppress(Exception):  # a refused read ends it just the same
                assert stream.read(1, timeout=2.0) == b""
            assert time.monotonic() - started < 1.0
        finally:
            listener.close()


class TestLoopback:
    """A server on this machine named `localhost`: dialled at IPv4 first,
    where the engines listen by default, then IPv6, each handshake under
    the loopback's own cap."""

    def test_localhost_reaches_a_server_listening_on_ipv4_alone(self) -> None:
        with _answering(socket.AF_INET, "127.0.0.1") as port:
            assert Http("test", {}).get(f"http://localhost:{port}/", purpose="t", timeout=5) == {}

    def test_localhost_reaches_a_server_listening_on_ipv6_alone(self) -> None:
        with _answering(socket.AF_INET6, "::1") as port:
            assert Http("test", {}).get(f"http://localhost:{port}/", purpose="t", timeout=5) == {}

    def test_localhost_is_dialled_ipv4_first_each_under_the_loopback_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked = _refusing(monkeypatch)
        with pytest.raises(httpcore.ConnectError):
            _Backend(None).connect_tcp("localhost", 9, timeout=2.0)
        assert asked == [
            ("127.0.0.1", _LOOPBACK_CONNECT_TIMEOUT),
            ("::1", _LOOPBACK_CONNECT_TIMEOUT),
        ]
        assert _LOOPBACK_CONNECT_TIMEOUT < 2.0

    def test_another_host_is_dialled_as_named_under_the_callers_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked = _refusing(monkeypatch)
        with pytest.raises(httpcore.ConnectError):
            _Backend(None).connect_tcp("example.com", 9, timeout=2.0)
        assert asked == [("example.com", 2.0)]


@contextlib.contextmanager
def _answering(family: socket.AddressFamily, host: str) -> Iterator[int]:
    """A server on `host` alone answering every request with `{}`; its port."""
    listener = socket.socket(family)
    try:
        listener.bind((host, 0))
    except OSError:
        listener.close()
        pytest.skip(f"no loopback at {host} on this machine")
    listener.listen(4)

    def answer() -> None:
        with contextlib.suppress(OSError):
            while True:
                conn = listener.accept()[0]
                with conn:
                    conn.recv(4096)
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Content-Length: 2\r\n\r\n{}"
                    )

    threading.Thread(target=answer, daemon=True).start()
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()


def _refusing(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float | None]]:
    """Every address the backend dials refused at once, each recorded
    with the handshake cap it was dialled under."""
    asked: list[tuple[str, float | None]] = []

    def refused(_self: Any, host: str, port: int, timeout: float | None = None, **_: Any) -> Any:
        asked.append((host, timeout))
        raise httpcore.ConnectError("refused")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", refused)
    return asked
