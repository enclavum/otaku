"""Tests for LMStudioClient — including the 200-for-errors and load
idempotence quirks documented in CLAUDE.md."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from otaku.client.lmstudio import LMStudioClient
from tests.support import make_provider

P = make_provider(name="lmstudio", url="http://lms/v1")
MODELS_URL = "http://lms/api/v1/models"


def _models(*items: dict) -> httpx.Response:
    return httpx.Response(200, json={"models": list(items)})


class TestMatches:
    @respx.mock
    def test_matches_when_first_item_has_key(self) -> None:
        respx.get(MODELS_URL).mock(return_value=_models({"key": "m"}))
        assert LMStudioClient.matches(P) is True

    @respx.mock
    def test_matches_empty_list(self) -> None:
        respx.get(MODELS_URL).mock(return_value=_models())
        assert LMStudioClient.matches(P) is True

    @respx.mock
    def test_rejects_error_body_with_200(self) -> None:
        # LM Studio returns 200 with an error body for unknown paths
        respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json={"error": "bad path"}))
        assert LMStudioClient.matches(P) is False

    @respx.mock
    def test_rejects_when_first_item_lacks_key(self) -> None:
        respx.get(MODELS_URL).mock(return_value=_models({"id": "m"}))
        assert LMStudioClient.matches(P) is False


class TestLoadedAndSizes:
    @respx.mock
    def test_loaded_needs_instances(self) -> None:
        respx.get(MODELS_URL).mock(
            return_value=_models(
                {"key": "loaded-one", "loaded_instances": [{"id": "i1"}]},
                {"key": "idle", "loaded_instances": []},
            )
        )
        assert LMStudioClient(P).loaded_models() == {"loaded-one"}

    @respx.mock
    def test_sizes(self) -> None:
        respx.get(MODELS_URL).mock(
            return_value=_models(
                {"key": "a", "size_bytes": 500}, {"key": "b", "size_bytes": 0}, {"key": "c"}
            )
        )
        assert LMStudioClient(P).model_sizes() == {"a": 500}


class TestLoadIdempotence:
    @respx.mock
    def test_skips_load_when_already_loaded(self) -> None:
        respx.get(MODELS_URL).mock(
            return_value=_models({"key": "m", "loaded_instances": [{"id": "i1"}]})
        )
        load = respx.post("http://lms/api/v1/models/load").mock(return_value=httpx.Response(200))
        LMStudioClient(P).load_model("m")
        assert load.called is False  # already loaded → no stacking

    @respx.mock
    def test_loads_when_not_loaded(self) -> None:
        respx.get(MODELS_URL).mock(return_value=_models({"key": "m", "loaded_instances": []}))
        load = respx.post("http://lms/api/v1/models/load").mock(return_value=httpx.Response(200))
        LMStudioClient(P).load_model("m")
        assert load.called is True
        assert json.loads(load.calls.last.request.content) == {"model": "m"}


class TestUnloadSweep:
    @respx.mock
    def test_unloads_every_matching_instance_by_id(self) -> None:
        respx.get(MODELS_URL).mock(
            return_value=_models(
                {"key": "m", "loaded_instances": [{"id": "i1"}, {"id": "i2"}]},
                {"key": "other", "loaded_instances": [{"id": "z"}]},
            )
        )
        unload = respx.post("http://lms/api/v1/models/unload").mock(
            return_value=httpx.Response(200)
        )
        LMStudioClient(P).unload_model("m")
        assert unload.call_count == 2
        sent_ids = [json.loads(c.request.content)["instance_id"] for c in unload.calls]
        assert sent_ids == ["i1", "i2"]


class TestContextSize:
    @respx.mock
    def test_prefers_instance_config(self) -> None:
        respx.get(MODELS_URL).mock(
            return_value=_models(
                {
                    "key": "m",
                    "loaded_instances": [{"config": {"context_length": 4096}}],
                    "max_context_length": 8192,
                }
            )
        )
        assert LMStudioClient(P).context_size("m") == 4096

    @respx.mock
    def test_falls_back_to_max_context(self) -> None:
        respx.get(MODELS_URL).mock(
            return_value=_models({"key": "m", "loaded_instances": [], "max_context_length": 8192})
        )
        assert LMStudioClient(P).context_size("m") == 8192


def test_kind() -> None:
    assert LMStudioClient.kind == "lmstudio"


class TestDefaultConfigSection:
    def test_default_port(self) -> None:  # no ~/.lmstudio in the isolated home
        section = LMStudioClient.default_config_section()
        assert 'url = "http://localhost:1234/v1"' in section
        assert "supports_thinking = false" in section

    def test_port_detected_from_settings(self) -> None:
        p = Path.home() / ".lmstudio/.internal/http-server-config.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"port": 4321}))
        assert 'url = "http://localhost:4321/v1"' in LMStudioClient.default_config_section()


@respx.mock
def test_unload_raises_on_error() -> None:
    respx.get(MODELS_URL).mock(
        return_value=_models({"key": "m", "loaded_instances": [{"id": "i"}]})
    )
    respx.post("http://lms/api/v1/models/unload").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        LMStudioClient(P).unload_model("m")
