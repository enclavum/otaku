"""LMStudioClient — load/unload/sizes/loaded-state via LM Studio's
/api/v1/models registry and /api/v1/models/{load,unload} endpoints.
"""

from __future__ import annotations

from typing import Any

import httpx

from otaku.client.base import (
    ProviderClient,
    base_url,
    get_json,
    headers,
    provider_config_section,
    read_home_json,
)
from otaku.config import Provider


class LMStudioClient(ProviderClient):
    """LM Studio (model registry + load/unload at /api/v1/models)."""

    kind = "lmstudio"

    @classmethod
    def matches(cls, provider: Provider) -> bool:
        data = get_json(provider, "/api/v1/models", timeout=0.5)
        if not isinstance(data, dict):
            return False
        models = data.get("models")
        # LM Studio returns 200 for unknown paths with an error body —
        # confirm the shape (`models` is a list of objects with a `key`).
        return isinstance(models, list) and (not models or "key" in (models[0] or {}))

    @classmethod
    def default_config_section(cls) -> str:
        port = read_home_json(".lmstudio/.internal/http-server-config.json").get("port")
        return provider_config_section(
            "lmstudio",
            port if isinstance(port, int) else 1234,
            extra=("supports_thinking = false",),
        )

    def _v1_models(self, timeout: float = 5.0) -> list[dict[str, Any]]:
        data = get_json(self.provider, "/api/v1/models", timeout=timeout)
        if isinstance(data, dict):
            models = data.get("models")
            if isinstance(models, list):
                return models
        return []

    def loaded_models(self, timeout: float = 1.5) -> set[str]:
        out: set[str] = set()
        for m in self._v1_models(timeout):
            key = m.get("key")
            instances = m.get("loaded_instances") or []
            if isinstance(key, str) and instances:
                out.add(key)
        return out

    def model_sizes(self, timeout: float = 5.0) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self._v1_models(timeout):
            key = m.get("key")
            size = m.get("size_bytes")
            if isinstance(key, str) and isinstance(size, int) and size > 0:
                out[key] = size
        return out

    def load_model(self, model: str) -> None:
        # Idempotent: LM Studio's /load is not — repeated calls stack
        # 'model:2', ':3', ... instances. Skip when already loaded.
        if model in self.loaded_models():
            return
        r = httpx.post(
            f"{base_url(self.provider)}/api/v1/models/load",
            json={"model": model},
            headers=headers(self.provider),
            timeout=None,
        )
        r.raise_for_status()

    def unload_model(self, model: str) -> None:
        # /unload takes 'instance_id'. Sweep every loaded instance whose
        # key matches `model` and unload each.
        instance_ids: list[str] = []
        for m in self._v1_models():
            if m.get("key") != model:
                continue
            for inst in m.get("loaded_instances") or []:
                inst_id = inst.get("id")
                if isinstance(inst_id, str):
                    instance_ids.append(inst_id)
        for inst_id in instance_ids:
            r = httpx.post(
                f"{base_url(self.provider)}/api/v1/models/unload",
                json={"instance_id": inst_id},
                headers=headers(self.provider),
                timeout=None,
            )
            r.raise_for_status()

    def _fetch_context_size(self, model: str) -> int | None:
        for m in self._v1_models():
            if m.get("key") != model:
                continue
            for inst in m.get("loaded_instances") or []:
                cfg = inst.get("config") or {}
                ctx = cfg.get("context_length")
                if isinstance(ctx, int) and ctx > 0:
                    return ctx
            ctx = m.get("max_context_length")
            if isinstance(ctx, int) and ctx > 0:
                return ctx
        return None
