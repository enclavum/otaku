"""Tests for OllamaClient native endpoints."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from otaku.client.ollama import OllamaClient
from tests.support import make_provider

P = make_provider(name="ollama", url="http://ollama/v1")


class TestMatches:
    @respx.mock
    def test_matches_valid_ps(self) -> None:
        respx.get("http://ollama/api/ps").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        assert OllamaClient.matches(P) is True

    @respx.mock
    def test_rejects_wrong_shape(self) -> None:
        respx.get("http://ollama/api/ps").mock(
            return_value=httpx.Response(200, json={"error": "nope"})
        )
        assert OllamaClient.matches(P) is False

    @respx.mock
    def test_rejects_non_200(self) -> None:
        respx.get("http://ollama/api/ps").mock(return_value=httpx.Response(404))
        assert OllamaClient.matches(P) is False


class TestLoadedModels:
    @respx.mock
    def test_reads_name_and_model_keys(self) -> None:
        respx.get("http://ollama/api/ps").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "a"}, {"model": "b"}, {}]})
        )
        assert OllamaClient(P).loaded_models() == {"a", "b"}

    @respx.mock
    def test_empty_on_bad_response(self) -> None:
        respx.get("http://ollama/api/ps").mock(return_value=httpx.Response(500))
        assert OllamaClient(P).loaded_models() == set()


class TestModelSizes:
    @respx.mock
    def test_reads_sizes(self) -> None:
        respx.get("http://ollama/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={
                    "models": [{"name": "a", "size": 100}, {"name": "b", "size": 0}, {"name": "c"}]
                },
            )
        )
        # size 0 and missing size are dropped
        assert OllamaClient(P).model_sizes() == {"a": 100}


class TestLoadUnload:
    @respx.mock
    def test_load_posts_keep_alive(self) -> None:
        route = respx.post("http://ollama/api/generate").mock(return_value=httpx.Response(200))
        OllamaClient(P).load_model("llama3")
        body = json.loads(route.calls.last.request.content)
        assert body["model"] == "llama3"
        assert body["keep_alive"] == "24h"
        assert body["stream"] is False

    @respx.mock
    def test_unload_posts_zero_keep_alive(self) -> None:
        route = respx.post("http://ollama/api/generate").mock(return_value=httpx.Response(200))
        OllamaClient(P).unload_model("llama3")
        body = json.loads(route.calls.last.request.content)
        assert body["model"] == "llama3"
        assert body["keep_alive"] == 0

    @respx.mock
    def test_load_raises_on_error(self) -> None:
        respx.post("http://ollama/api/generate").mock(return_value=httpx.Response(500))
        import pytest

        with pytest.raises(httpx.HTTPStatusError):
            OllamaClient(P).load_model("m")


class TestContextSize:
    @respx.mock
    def test_reads_context_length(self) -> None:
        respx.get("http://ollama/api/ps").mock(
            return_value=httpx.Response(
                200, json={"models": [{"name": "m", "context_length": 8192}]}
            )
        )
        assert OllamaClient(P).context_size("m") == 8192

    @respx.mock
    def test_none_when_model_absent(self) -> None:
        respx.get("http://ollama/api/ps").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        assert OllamaClient(P).context_size("m") is None


def test_kind() -> None:
    assert OllamaClient.kind == "ollama"


class TestDefaultConfigSection:
    def test_default_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        section = OllamaClient.default_config_section()
        assert "[providers.ollama]" in section
        assert 'url = "http://localhost:11434/v1"' in section
        assert "supports_thinking = true" in section
        assert 'keep_alive = "24h"' in section

    def test_port_detected_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9000")
        assert 'url = "http://localhost:9000/v1"' in OllamaClient.default_config_section()

    def test_bad_env_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "not-a-port")
        assert 'url = "http://localhost:11434/v1"' in OllamaClient.default_config_section()
