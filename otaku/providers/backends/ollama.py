"""Ollama: load/unload, sizes, and loaded-state via the native /api
endpoints; chat rides the OpenAI protocol at /v1."""

import os

import httpx

from otaku.providers.base import ManagedClient
from otaku.settings.config import Provider


class OllamaClient(ManagedClient):
    kind = "ollama"

    @classmethod
    def autoconfigure(cls) -> Provider:
        """The first-run section, its port detected from OLLAMA_HOST."""
        host = os.environ.get("OLLAMA_HOST")
        port = (_parse_port(host) if host else None) or 11434
        url = f"http://localhost:{port}/v1"
        return Provider(name=cls.kind, url=url, supports_thinking=True, keep_alive="24h")

    def get_loaded_models(self, timeout: float = 1.5) -> set[str]:
        loaded: set[str] = set()
        data = self._get_json("/api/ps", timeout=timeout)
        if not isinstance(data, dict):
            return loaded
        for entry in data.get("models") or []:
            name = entry.get("name") or entry.get("model")
            if name:
                loaded.add(str(name))
        return loaded

    def get_model_sizes(self, timeout: float = 5.0) -> dict[str, int]:
        sizes: dict[str, int] = {}
        data = self._get_json("/api/tags", timeout=timeout)
        if not isinstance(data, dict):
            return sizes
        for entry in data.get("models") or []:
            name = entry.get("name") or entry.get("model")
            size = entry.get("size")
            if isinstance(name, str) and isinstance(size, int) and size > 0:
                sizes[name] = size
        return sizes

    def load_model(self, model: str) -> None:
        body = {
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": self.provider.keep_alive or "24h",
        }
        response = httpx.post(
            f"{self.provider.base_url}/api/generate",
            json=body,
            headers=self.provider.headers,
            timeout=None,
        )
        response.raise_for_status()

    def unload_model(self, model: str) -> None:
        body = {"model": model, "prompt": "", "stream": False, "keep_alive": 0}
        response = httpx.post(
            f"{self.provider.base_url}/api/generate",
            json=body,
            headers=self.provider.headers,
            timeout=None,
        )
        response.raise_for_status()

    def _fetch_context_size(self, model: str) -> int | None:
        data = self._get_json("/api/ps", timeout=1.5)
        if not isinstance(data, dict):
            return None
        for entry in data.get("models") or []:
            named = entry.get("name") == model or entry.get("model") == model
            if named and isinstance(entry.get("context_length"), int):
                return int(entry["context_length"])
        return None


def _parse_port(value: str) -> int | None:
    """The port of a `host:port`, `:port`, `http://host:port`, or bare
    `port` string; None when the tail is not a valid port."""
    try:
        port = int(value.rsplit(":", 1)[-1].strip())
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None
