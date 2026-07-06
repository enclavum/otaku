"""Tests for the base ProviderClient (OpenAI-compat) + module helpers."""

from __future__ import annotations

import httpx
import pytest
import respx

from otaku.client import ContentDelta, FinalStats, ThinkingDelta
from otaku.client.base import (
    ProviderClient,
    base_url,
    get_json,
    headers,
    port_from_listen,
    provider_config_section,
)
from otaku.storage.store import Message
from tests.support import content_chunk, make_provider, sse, thinking_chunk, usage_chunk


class TestConfigDetectionHelpers:
    @pytest.mark.parametrize(
        ("s", "expected"),
        [
            ("11434", 11434),
            (":11434", 11434),
            ("0.0.0.0:11434", 11434),
            ("http://localhost:11434", 11434),
            ("localhost", None),
            ("host:99999", None),
            ("host:0", None),
            ("", None),
        ],
    )
    def test_port_from_listen(self, s: str, expected: int | None) -> None:
        assert port_from_listen(s) == expected

    def test_provider_config_section_format(self) -> None:
        section = provider_config_section("x", 9000, api_key="k", extra=("flag = true",))
        assert section == (
            '[providers.x]\nurl = "http://localhost:9000/v1"\napi_key = "k"\nflag = true\n'
        )

    def test_base_client_has_no_default_section(self) -> None:
        assert ProviderClient.default_config_section() is None


class TestUrlHelpers:
    def test_base_url_strips_v1(self) -> None:
        assert base_url(make_provider(url="http://h:1/v1")) == "http://h:1"

    def test_base_url_without_v1_unchanged(self) -> None:
        assert base_url(make_provider(url="http://h:1")) == "http://h:1"

    def test_headers_with_api_key(self) -> None:
        assert headers(make_provider(api_key="sk-1")) == {"Authorization": "Bearer sk-1"}

    def test_headers_without_api_key(self) -> None:
        assert headers(make_provider()) == {}


class TestGetJson:
    @respx.mock
    def test_returns_json_on_200(self) -> None:
        p = make_provider(url="http://h/v1")
        respx.get("http://h/api/thing").mock(return_value=httpx.Response(200, json={"ok": 1}))
        assert get_json(p, "/api/thing") == {"ok": 1}

    @respx.mock
    def test_returns_none_on_non_200(self) -> None:
        p = make_provider(url="http://h/v1")
        respx.get("http://h/api/thing").mock(return_value=httpx.Response(404, json={"e": 1}))
        assert get_json(p, "/api/thing") is None

    @respx.mock
    def test_returns_none_on_network_error(self) -> None:
        p = make_provider(url="http://h/v1")
        respx.get("http://h/api/thing").mock(side_effect=httpx.ConnectError("boom"))
        assert get_json(p, "/api/thing") is None


class TestListModels:
    @respx.mock
    def test_returns_sorted_ids(self) -> None:
        p = make_provider(url="http://h/v1")
        respx.get("http://h/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "zeta"}, {"id": "alpha"}]})
        )
        assert ProviderClient(p).list_models() == ["alpha", "zeta"]

    @respx.mock
    def test_empty_data(self) -> None:
        p = make_provider(url="http://h/v1")
        respx.get("http://h/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))
        assert ProviderClient(p).list_models() == []

    @respx.mock
    def test_raises_on_http_error(self) -> None:
        p = make_provider(url="http://h/v1")
        respx.get("http://h/v1/models").mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            ProviderClient(p).list_models()


class TestChatStream:
    @respx.mock
    def test_yields_thinking_content_and_stats(self) -> None:
        p = make_provider(url="http://h/v1")
        body = sse(
            thinking_chunk("let me think"),
            content_chunk("hello"),
            content_chunk(" world"),
            usage_chunk(3, 5),
            "[DONE]",
        )
        respx.post("http://h/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=body)
        )
        chunks = list(ProviderClient(p).chat_stream("m", [Message("user", "hi")], {}))

        assert chunks[0] == ThinkingDelta("let me think")
        assert chunks[1] == ContentDelta("hello")
        assert chunks[2] == ContentDelta(" world")
        final = chunks[-1]
        assert isinstance(final, FinalStats)
        assert final.prompt_tokens == 3
        assert final.completion_tokens == 5
        assert final.duration_seconds >= 0

    @respx.mock
    def test_reasoning_key_alias(self) -> None:
        p = make_provider(url="http://h/v1")
        body = sse({"choices": [{"delta": {"reasoning": "hmm"}}]}, "[DONE]")
        respx.post("http://h/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=body)
        )
        chunks = list(ProviderClient(p).chat_stream("m", [], {}))
        assert ThinkingDelta("hmm") in chunks

    @respx.mock
    def test_ignores_non_data_and_malformed_lines(self) -> None:
        p = make_provider(url="http://h/v1")
        raw = b"event: ping\n\ndata: {bad json\n\n" + sse(content_chunk("ok"), "[DONE]")
        respx.post("http://h/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=raw)
        )
        contents = [
            c.text
            for c in ProviderClient(p).chat_stream("m", [], {})
            if isinstance(c, ContentDelta)
        ]
        assert contents == ["ok"]

    @respx.mock
    def test_request_body_shape(self) -> None:
        p = make_provider(url="http://h/v1")
        route = respx.post("http://h/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse("[DONE]"))
        )
        list(
            ProviderClient(p).chat_stream(
                "mymodel", [Message("system", "s"), Message("user", "u")], {"temperature": 0.5}
            )
        )
        import json

        sent = json.loads(route.calls.last.request.content)
        assert sent["model"] == "mymodel"
        assert sent["stream"] is True
        assert sent["stream_options"] == {"include_usage": True}
        assert sent["temperature"] == 0.5
        assert sent["messages"] == [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]

    @respx.mock
    def test_thinking_sent_when_supported(self) -> None:
        p = make_provider(url="http://h/v1", supports_thinking=True)
        route = respx.post("http://h/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse("[DONE]"))
        )
        list(ProviderClient(p).chat_stream("m", [], {}, think="high"))
        import json

        assert json.loads(route.calls.last.request.content)["reasoning_effort"] == "high"

    @respx.mock
    def test_thinking_omitted_when_unsupported(self) -> None:
        p = make_provider(url="http://h/v1", supports_thinking=False)
        route = respx.post("http://h/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse("[DONE]"))
        )
        list(ProviderClient(p).chat_stream("m", [], {}, think="high"))
        import json

        assert "reasoning_effort" not in json.loads(route.calls.last.request.content)


class TestApplyThinking:
    def test_none_sends_nothing(self) -> None:
        body: dict[str, object] = {}
        ProviderClient(make_provider(supports_thinking=True))._apply_thinking(body, None)
        assert body == {}

    def test_level_sent_when_supported(self) -> None:
        body: dict[str, object] = {}
        ProviderClient(make_provider(supports_thinking=True))._apply_thinking(body, "medium")
        assert body == {"reasoning_effort": "medium"}

    def test_not_sent_when_unsupported(self) -> None:
        body: dict[str, object] = {}
        ProviderClient(make_provider(supports_thinking=False))._apply_thinking(body, "medium")
        assert body == {}


class TestDefaultHooks:
    def test_loaded_models_empty(self) -> None:
        assert ProviderClient(make_provider()).loaded_models() == set()

    def test_model_sizes_empty(self) -> None:
        assert ProviderClient(make_provider()).model_sizes() == {}

    def test_load_unload_not_implemented(self) -> None:
        c = ProviderClient(make_provider())
        with pytest.raises(NotImplementedError):
            c.load_model("m")
        with pytest.raises(NotImplementedError):
            c.unload_model("m")

    def test_matches_is_true(self) -> None:
        assert ProviderClient.matches(make_provider()) is True

    def test_context_size_default_none_and_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = ProviderClient(make_provider())
        calls = {"n": 0}

        def fake_fetch(model: str) -> int | None:
            calls["n"] += 1
            return None

        monkeypatch.setattr(c, "_fetch_context_size", fake_fetch)
        assert c.context_size("m") is None
        assert c.context_size("m") is None
        assert calls["n"] == 1  # result cached, fetched once
