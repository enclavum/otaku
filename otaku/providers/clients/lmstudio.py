"""LM Studio: the model registry, load/unload, sizes, and context windows
via its /api/v1/models surface; chat rides the OpenAI protocol at /v1."""

import json
from pathlib import Path
from typing import Any

import httpx

from otaku.providers.base import ManagedClient, ModelInfo
from otaku.settings.config import ProviderConfig


class LmStudioClient(ManagedClient):
    kind = "lmstudio"
    supports_thinking = False  # no request-level knob; reasoning is per-model

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The first-run section, its port detected from LM Studio's own
        server config file."""
        port = _read_home_json(".lmstudio/.internal/http-server-config.json").get("port")
        url = f"http://localhost:{port if isinstance(port, int) else 1234}/v1"
        return ProviderConfig(name=cls.kind, url=url)

    def load_model(self, model: str) -> None:
        # Idempotent on purpose: LM Studio's /load is not — repeated calls
        # stack 'model:2', ':3', … instances. Skip when already loaded.
        if any(row.name == model and row.loaded for row in self.models(timeout=5.0)):
            return
        response = httpx.post(
            f"{self.provider_config.base_url}/api/v1/models/load",
            json={"model": model},
            headers=self.provider_config.headers,
            timeout=None,
        )
        response.raise_for_status()

    def unload_model(self, model: str) -> None:
        # /unload takes an instance id: sweep every loaded instance whose
        # key matches `model` and unload each.
        for entry in self._registry(timeout=5.0):
            if entry.get("key") != model:
                continue
            for instance in entry.get("loaded_instances") or []:
                instance_id = instance.get("id")
                if not isinstance(instance_id, str):
                    continue
                response = httpx.post(
                    f"{self.provider_config.base_url}/api/v1/models/unload",
                    json={"instance_id": instance_id},
                    headers=self.provider_config.headers,
                    timeout=None,
                )
                response.raise_for_status()

    def _list(self, timeout: float) -> list[ModelInfo]:
        """One row per model, everything from the one /api/v1/models pass:
        key, size, loaded instances, and the context window (a loaded
        instance's configured window beats the model's maximum). No such
        surface — not LM Studio — falls to the plain names; a dead server
        raises out of them."""
        entries = self._registry(timeout)
        if not entries:
            return [ModelInfo(name=name) for name in self._model_names(timeout)]
        rows = []
        for entry in entries:
            key = entry.get("key")
            if not isinstance(key, str):
                continue
            size = entry.get("size_bytes")
            rows.append(
                ModelInfo(
                    name=key,
                    size=size if isinstance(size, int) and size > 0 else None,
                    context=_context_of(entry),
                    loaded=bool(entry.get("loaded_instances")),
                )
            )
        return sorted(rows, key=lambda row: row.name)

    def _fetch_context_size(self, model: str) -> int | None:
        for entry in self._registry(timeout=1.5):
            if entry.get("key") == model:
                return _context_of(entry)
        return None

    def _registry(self, timeout: float) -> list[dict[str, Any]]:
        data = self._get_json("/api/v1/models", timeout=timeout)
        if isinstance(data, dict):
            models = data.get("models")
            if isinstance(models, list):
                return models
        return []


def _context_of(entry: dict[str, Any]) -> int | None:
    """The context window of one registry entry: a loaded instance's
    configured length when there is one, the model's maximum otherwise."""
    for instance in entry.get("loaded_instances") or []:
        config = instance.get("config") or {}
        context = config.get("context_length")
        if isinstance(context, int) and context > 0:
            return context
    context = entry.get("max_context_length")
    return context if isinstance(context, int) and context > 0 else None


def _read_home_json(relative: str) -> dict[str, Any]:
    """A JSON object at `~/<relative>`, or {} on any failure."""
    try:
        parsed = json.loads((Path.home() / relative).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
