"""Ollama: the model registry, load/unload, sizes, and context windows via
the native /api endpoints; chat rides the OpenAI protocol at /v1."""

import os

import httpx

from otaku.providers.base import ManagedClient, ModelInfo
from otaku.settings.config import ProviderConfig


class OllamaClient(ManagedClient):
    kind = "ollama"

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The first-run section, its host and port detected from
        OLLAMA_HOST — a remote server stays remote."""
        raw = os.environ.get("OLLAMA_HOST") or ""
        host = _parse_host(raw) or "localhost"
        port = _parse_port(raw) or 11434
        url = f"http://{host}:{port}/v1"
        return ProviderConfig(name=cls.kind, url=url, keep_alive="24h")

    def load_model(self, model: str) -> None:
        body = {
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": self.provider_config.keep_alive or "24h",
        }
        response = httpx.post(
            f"{self.provider_config.base_url}/api/generate",
            json=body,
            headers=self.provider_config.headers,
            timeout=None,
        )
        response.raise_for_status()

    def unload_model(self, model: str) -> None:
        body = {"model": model, "prompt": "", "stream": False, "keep_alive": 0}
        response = httpx.post(
            f"{self.provider_config.base_url}/api/generate",
            json=body,
            headers=self.provider_config.headers,
            timeout=None,
        )
        response.raise_for_status()

    def _list(self, timeout: float) -> list[ModelInfo]:
        """One row per model: names and sizes from /api/tags, loaded state
        from /api/ps, context windows through the per-model cache (the
        live window for a loaded model, /api/show's static one otherwise).
        A loaded model missing from the registry still belongs in the
        list."""
        sizes: dict[str, int] = {}
        data = self._get_json("/api/tags", timeout=timeout)
        if data is None:
            raise httpx.ConnectError(f"{self.provider_config.name} is not answering /api/tags")
        for entry in data.get("models") or [] if isinstance(data, dict) else []:
            name = entry.get("name") or entry.get("model")
            size = entry.get("size")
            if isinstance(name, str):
                sizes[name] = size if isinstance(size, int) and size > 0 else 0
        loaded = self._loaded(timeout=1.5)
        names = sorted(sizes) + sorted(loaded - set(sizes))
        return [
            ModelInfo(
                name=name,
                size=sizes.get(name) or None,
                context=self.get_context_size(name),
                loaded=name in loaded,
            )
            for name in names
        ]

    def _loaded(self, timeout: float) -> set[str]:
        loaded: set[str] = set()
        data = self._get_json("/api/ps", timeout=timeout)
        if not isinstance(data, dict):
            return loaded
        for entry in data.get("models") or []:
            name = entry.get("name") or entry.get("model")
            if name:
                loaded.add(str(name))
        return loaded

    def _fetch_context_size(self, model: str) -> int | None:
        # The live window of a loaded model first — num_ctx at load time
        # beats the model card — then /api/show's static card value.
        data = self._get_json("/api/ps", timeout=1.5)
        if isinstance(data, dict):
            for entry in data.get("models") or []:
                named = entry.get("name") == model or entry.get("model") == model
                if named and isinstance(entry.get("context_length"), int):
                    return int(entry["context_length"])
        shown = self._post_json("/api/show", {"model": model}, timeout=1.5)
        if isinstance(shown, dict):
            info = shown.get("model_info")
            if isinstance(info, dict):
                for key, value in info.items():
                    if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                        return value
        return None


def _parse_host(value: str) -> str | None:
    """The host of a `host:port`, `http://host[:port]`, or bare `host`
    string; None when there is none (`:port`, a bare port, empty)."""
    trimmed = value.strip()
    for scheme in ("http://", "https://"):
        if trimmed.startswith(scheme):
            trimmed = trimmed[len(scheme) :]
            break
    trimmed = trimmed.split("/", 1)[0]
    host, colon, _ = trimmed.rpartition(":")
    if not colon:
        return None if not trimmed or trimmed.isdigit() else trimmed
    return host or None


def _parse_port(value: str) -> int | None:
    """The port of a `host:port`, `:port`, `http://host:port`, or bare
    `port` string; None when the tail is not a valid port."""
    try:
        port = int(value.rsplit(":", 1)[-1].strip())
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None
