"""OllamaClient — load/unload/sizes/loaded-state via Ollama's native
endpoints (/api/ps, /api/tags, /api/generate).
"""

from __future__ import annotations

import os

import httpx

from otaku.client.base import (
    ProviderClient,
    base_url,
    get_json,
    headers,
    port_from_listen,
    provider_config_section,
)
from otaku.config import Provider


class OllamaClient(ProviderClient):
    """Native Ollama backend (model swap via /api/generate keep_alive)."""

    kind = "ollama"

    @classmethod
    def matches(cls, provider: Provider) -> bool:
        data = get_json(provider, "/api/ps", timeout=0.5)
        return isinstance(data, dict) and isinstance(data.get("models"), list)

    @classmethod
    def default_config_section(cls) -> str:
        host = os.environ.get("OLLAMA_HOST")
        port = (port_from_listen(host) if host else None) or 11434
        return provider_config_section(
            "ollama", port, extra=("supports_thinking = true", 'keep_alive = "24h"')
        )

    def loaded_models(self, timeout: float = 1.5) -> set[str]:
        out: set[str] = set()
        data = get_json(self.provider, "/api/ps", timeout=timeout)
        if not isinstance(data, dict):
            return out
        for m in data.get("models") or []:
            name = m.get("name") or m.get("model")
            if name:
                out.add(str(name))
        return out

    def model_sizes(self, timeout: float = 5.0) -> dict[str, int]:
        out: dict[str, int] = {}
        data = get_json(self.provider, "/api/tags", timeout=timeout)
        if not isinstance(data, dict):
            return out
        for m in data.get("models") or []:
            name = m.get("name") or m.get("model")
            size = m.get("size")
            if isinstance(name, str) and isinstance(size, int) and size > 0:
                out[name] = size
        return out

    def load_model(self, model: str) -> None:
        body = {
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": self.provider.keep_alive,
        }
        r = httpx.post(
            f"{base_url(self.provider)}/api/generate",
            json=body,
            headers=headers(self.provider),
            timeout=None,
        )
        r.raise_for_status()

    def unload_model(self, model: str) -> None:
        body = {"model": model, "prompt": "", "stream": False, "keep_alive": 0}
        r = httpx.post(
            f"{base_url(self.provider)}/api/generate",
            json=body,
            headers=headers(self.provider),
            timeout=None,
        )
        r.raise_for_status()

    def _fetch_context_size(self, model: str) -> int | None:
        data = get_json(self.provider, "/api/ps", timeout=1.5)
        if not isinstance(data, dict):
            return None
        for m in data.get("models") or []:
            if (m.get("name") == model or m.get("model") == model) and isinstance(
                m.get("context_length"), int
            ):
                return int(m["context_length"])
        return None
