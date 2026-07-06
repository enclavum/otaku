"""Tests for OmlxClient — status registry, URL-quoted load/unload, and the
enable_thinking translation."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from otaku.client import ContentDelta, FinalStats, ThinkingDelta
from otaku.client.omlx import OmlxClient
from tests.support import content_chunk, make_provider, sse

P = make_provider(name="omlx", url="http://omlx/v1")
STATUS_URL = "http://omlx/v1/models/status"


def _fast_omlx(*, smoothen_streaming: bool = True) -> OmlxClient:
    """Smoothing client with a tiny tick/window so tests pace in ~ms."""
    c = OmlxClient(
        make_provider(name="omlx", url="http://omlx/v1", smoothen_streaming=smoothen_streaming)
    )
    c._SMOOTH_TICK = 0.001
    c._SMOOTH_WINDOW = 0.004  # tick/window ratio = 0.25
    return c


def _status(*items: dict) -> httpx.Response:
    return httpx.Response(200, json={"models": list(items)})


class TestMatches:
    @respx.mock
    def test_matches_when_item_has_loaded_flag(self) -> None:
        respx.get(STATUS_URL).mock(return_value=_status({"id": "m", "loaded": True}))
        assert OmlxClient.matches(P) is True

    @respx.mock
    def test_rejects_error_body(self) -> None:
        respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json={"error": "x"}))
        assert OmlxClient.matches(P) is False

    @respx.mock
    def test_rejects_item_without_loaded(self) -> None:
        respx.get(STATUS_URL).mock(return_value=_status({"id": "m"}))
        assert OmlxClient.matches(P) is False


class TestLoadedAndSizes:
    @respx.mock
    def test_loaded_models(self) -> None:
        respx.get(STATUS_URL).mock(
            return_value=_status({"id": "a", "loaded": True}, {"id": "b", "loaded": False})
        )
        assert OmlxClient(P).loaded_models() == {"a"}

    @respx.mock
    def test_sizes_prefers_actual_over_estimated(self) -> None:
        respx.get(STATUS_URL).mock(
            return_value=_status(
                {"id": "a", "actual_size": 10, "estimated_size": 99},
                {"id": "b", "estimated_size": 20},
            )
        )
        assert OmlxClient(P).model_sizes() == {"a": 10, "b": 20}


class TestLoadUnload:
    @respx.mock
    def test_load_url_is_quoted(self) -> None:
        route = respx.post("http://omlx/v1/models/org%2Fmodel/load").mock(
            return_value=httpx.Response(200)
        )
        OmlxClient(P).load_model("org/model")
        assert route.called is True

    @respx.mock
    def test_unload_url_is_quoted(self) -> None:
        route = respx.post("http://omlx/v1/models/org%2Fmodel/unload").mock(
            return_value=httpx.Response(200)
        )
        OmlxClient(P).unload_model("org/model")
        assert route.called is True


class TestApplyThinking:
    def test_none_leaves_default(self) -> None:
        body: dict[str, object] = {}
        OmlxClient(P)._apply_thinking(body, None)
        assert body == {}

    def test_none_level_disables(self) -> None:
        body: dict[str, object] = {}
        OmlxClient(P)._apply_thinking(body, "none")
        assert body == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_level_enables(self) -> None:
        body: dict[str, object] = {}
        OmlxClient(P)._apply_thinking(body, "high")
        assert body == {"chat_template_kwargs": {"enable_thinking": True}}

    def test_disables_regardless_of_supports_thinking(self) -> None:
        # supports_thinking False must still send enable_thinking:false so "off" takes
        body: dict[str, object] = {}
        OmlxClient(make_provider(url="http://omlx/v1", supports_thinking=False))._apply_thinking(
            body, "none"
        )
        assert body == {"chat_template_kwargs": {"enable_thinking": False}}


class TestContextSize:
    @respx.mock
    def test_reads_max_context_window(self) -> None:
        respx.get(STATUS_URL).mock(
            return_value=_status({"id": "m", "loaded": True, "max_context_window": 32768})
        )
        assert OmlxClient(P).context_size("m") == 32768


def test_kind() -> None:
    assert OmlxClient.kind == "omlx"


class TestDefaultConfigSection:
    def test_defaults(self) -> None:  # no ~/.omlx in the isolated home
        section = OmlxClient.default_config_section()
        assert 'url = "http://localhost:8000/v1"' in section
        assert 'api_key = ""' in section
        assert "smoothen_streaming = true" in section

    def test_port_and_key_detected_from_settings(self) -> None:
        p = Path.home() / ".omlx/settings.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"server": {"port": 10240}, "auth": {"api_key": "sk-omlx"}}))
        section = OmlxClient.default_config_section()
        assert 'url = "http://localhost:10240/v1"' in section
        assert 'api_key = "sk-omlx"' in section


class TestSmoothing:
    def test_drain_count_proportional_min_one(self) -> None:
        c = _fast_omlx()  # tick/window = 0.25
        assert c._drain_count(0) == 1
        assert c._drain_count(1) == 1  # ceil(0.25)
        assert c._drain_count(4) == 1  # ceil(1.0)
        assert c._drain_count(40) == 10  # ceil(10.0)

    def test_rechunks_preserving_content_and_stats(self) -> None:
        c = _fast_omlx()
        text = "hello world, this is a fairly long burst of tokens"

        def base():
            yield ContentDelta(text)
            yield FinalStats(prompt_tokens=3, completion_tokens=9, duration_seconds=0.2)

        out = list(c._smooth(base()))
        assert "".join(x.text for x in out if isinstance(x, ContentDelta)) == text
        assert isinstance(out[-1], FinalStats) and out[-1].completion_tokens == 9
        # the single burst was paced into multiple smaller deltas
        assert sum(isinstance(x, ContentDelta) for x in out) >= 2

    def test_thinking_passes_through(self) -> None:
        c = _fast_omlx()

        def base():
            yield ThinkingDelta("reasoning")
            yield ContentDelta("answer")
            yield FinalStats(None, 2, 0.1)

        out = list(c._smooth(base()))
        assert any(isinstance(x, ThinkingDelta) and x.text == "reasoning" for x in out)
        assert "".join(x.text for x in out if isinstance(x, ContentDelta)) == "answer"

    def test_error_propagates_after_buffered_content(self) -> None:
        c = _fast_omlx()

        def base():
            yield ContentDelta("partial")
            raise httpx.ConnectError("down")

        collected: list = []
        with pytest.raises(httpx.ConnectError):
            for x in c._smooth(base()):
                collected.append(x)
        assert "".join(x.text for x in collected if isinstance(x, ContentDelta)) == "partial"

    @respx.mock
    def test_passthrough_when_smooth_off(self) -> None:
        body = sse(content_chunk("hi"), content_chunk(" there"), "[DONE]")
        respx.post("http://omlx/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=body)
        )
        respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json={"models": []}))
        client = OmlxClient(
            make_provider(name="omlx", url="http://omlx/v1", smoothen_streaming=False)
        )
        chunks = list(client.chat_stream("m", [], {}))
        # identical to base: the two source deltas, not re-chunked
        assert [c.text for c in chunks if isinstance(c, ContentDelta)] == ["hi", " there"]

    @respx.mock
    def test_smooth_on_rechunks_over_http(self) -> None:
        text = "hello world this is a longish streamed response"
        body = sse(content_chunk(text), "[DONE]")
        respx.post("http://omlx/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=body)
        )
        respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json={"models": []}))
        chunks = list(_fast_omlx().chat_stream("m", [], {}))
        assert "".join(c.text for c in chunks if isinstance(c, ContentDelta)) == text
        assert sum(isinstance(c, ContentDelta) for c in chunks) >= 2
