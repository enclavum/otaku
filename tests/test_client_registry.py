"""Tests for client_for's probe chain + cache and the provider fan-out helpers."""

from __future__ import annotations

import sys

import httpx
import respx

from otaku.client import (
    client_for,
    map_providers,
    probing_notice,
    unreachable_help,
)
from otaku.client.base import ProviderClient
from otaku.client.lmstudio import LMStudioClient
from otaku.client.ollama import OllamaClient
from otaku.client.omlx import OmlxClient
from tests.support import make_provider


def _probe_routes(*, ps=None, lms=None, omlx=None) -> None:
    """Register all three native probe endpoints. Each arg is the JSON body to
    return (matching shape) or None → 404 (no match)."""
    for url, body in (
        ("http://h/api/ps", ps),
        ("http://h/api/v1/models", lms),
        ("http://h/v1/models/status", omlx),
    ):
        if body is None:
            respx.get(url).mock(return_value=httpx.Response(404))
        else:
            respx.get(url).mock(return_value=httpx.Response(200, json=body))


P = make_provider(name="h", url="http://h/v1")


class TestProbeChain:
    @respx.mock
    def test_matches_ollama(self) -> None:
        _probe_routes(ps={"models": []})
        assert isinstance(client_for(P), OllamaClient)

    @respx.mock
    def test_matches_lmstudio(self) -> None:
        _probe_routes(ps=None, lms={"models": [{"key": "m"}]})
        assert isinstance(client_for(P), LMStudioClient)

    @respx.mock
    def test_matches_omlx(self) -> None:
        _probe_routes(ps=None, lms=None, omlx={"models": [{"id": "m", "loaded": True}]})
        assert isinstance(client_for(P), OmlxClient)

    @respx.mock
    def test_falls_through_to_base(self) -> None:
        _probe_routes(ps=None, lms=None, omlx=None)
        c = client_for(P)
        assert type(c) is ProviderClient


class TestCache:
    @respx.mock
    def test_result_is_cached_per_provider_name(self) -> None:
        _probe_routes(ps={"models": []})
        first = client_for(P)
        # A second call must not re-probe — clear the routes so any HTTP would
        # error, then confirm the same cached instance comes back.
        respx.reset()
        second = client_for(P)
        assert first is second


class TestMapProviders:
    def test_preserves_mapping_order(self) -> None:
        provs = {n: make_provider(name=n) for n in ("a", "b", "c")}
        assert map_providers(provs, lambda name, _p: name) == ["a", "b", "c"]

    def test_empty_returns_empty(self) -> None:
        assert map_providers({}, lambda name, _p: name) == []

    def test_runs_concurrently(self) -> None:
        # Three tasks that each block on a barrier only all-clear if they run at
        # the same time — a serial map would deadlock (caught by the timeout).
        import threading

        provs = {n: make_provider(name=n) for n in ("a", "b", "c")}
        barrier = threading.Barrier(3, timeout=5)
        assert sorted(map_providers(provs, lambda name, _p: (barrier.wait(), name)[1])) == [
            "a",
            "b",
            "c",
        ]

    def test_fn_exception_propagates(self) -> None:
        import pytest

        def boom(name: str, _p: object) -> str:
            raise RuntimeError(name)

        with pytest.raises(RuntimeError):
            map_providers({"a": make_provider(name="a")}, boom)


class TestUnreachableHelp:
    def test_marks_down_and_responding_and_points_at_config(self) -> None:
        provs = {
            "ollama": make_provider(name="ollama", url="http://localhost:11434/v1"),
            "lmstudio": make_provider(name="lmstudio", url="http://localhost:1234/v1"),
        }
        msg = unreachable_help(provs, reachable={"lmstudio"})
        assert "No models reachable" in msg
        assert "✗ ollama → http://localhost:11434/v1" in msg
        assert "not responding" in msg
        assert "✓ lmstudio → http://localhost:1234/v1" in msg
        assert "responding, but exposes no models" in msg
        assert "~/.otaku/config.toml" in msg  # tells the user where to fix it, ~-shortened


class _FakeStderr:
    def __init__(self, tty: bool) -> None:
        self._tty = tty
        self.buf: list[str] = []

    def isatty(self) -> bool:
        return self._tty

    def write(self, s: str) -> int:
        self.buf.append(s)
        return len(s)

    def flush(self) -> None:
        pass


class TestProbingNotice:
    def test_shows_dim_line_then_erases_on_tty(self, monkeypatch) -> None:
        fake = _FakeStderr(tty=True)
        monkeypatch.setattr(sys, "stderr", fake)
        provs = {"ollama": make_provider("ollama"), "omlx": make_provider("omlx")}
        with probing_notice(provs):
            pass
        out = "".join(fake.buf)
        assert "Looking for providers" in out
        assert "~/.otaku/config.toml" in out  # config path, ~-shortened
        assert "\x1b[2m" in out and "\x1b[0m" in out  # dim + reset
        assert out.endswith("\r\x1b[2K")  # transient — erased when done

    def test_noop_when_stderr_not_a_tty(self, monkeypatch) -> None:
        fake = _FakeStderr(tty=False)
        monkeypatch.setattr(sys, "stderr", fake)
        with probing_notice({"ollama": make_provider("ollama")}):
            pass
        assert fake.buf == []  # never pollutes piped/redirected output
