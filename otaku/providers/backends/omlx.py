"""omlx: an MLX model server speaking the OpenAI protocol for chat, with
its own model registry and load/unload surface under /v1/models."""

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from otaku.providers.base import ManagedClient
from otaku.settings.config import Provider


class OmlxClient(ManagedClient):
    kind = "omlx"

    @classmethod
    def autoconfigure(cls) -> Provider:
        """The first-run section, its port and api key detected from omlx's
        own settings file."""
        settings = _read_home_json(".omlx/settings.json")
        server = settings.get("server")
        port = server.get("port") if isinstance(server, dict) else None
        auth = settings.get("auth")
        key = auth.get("api_key") if isinstance(auth, dict) else None
        url = f"http://localhost:{port if isinstance(port, int) else 8000}/v1"
        return Provider(name=cls.kind, url=url, api_key=str(key) if key else "")

    def get_loaded_models(self, timeout: float = 1.5) -> set[str]:
        loaded: set[str] = set()
        for entry in self._status_models(timeout):
            model_id = entry.get("id")
            if isinstance(model_id, str) and entry.get("loaded"):
                loaded.add(model_id)
        return loaded

    def get_model_sizes(self, timeout: float = 5.0) -> dict[str, int]:
        sizes: dict[str, int] = {}
        for entry in self._status_models(timeout):
            model_id = entry.get("id")
            size = entry.get("actual_size") or entry.get("estimated_size")
            if isinstance(model_id, str) and isinstance(size, int) and size > 0:
                sizes[model_id] = size
        return sizes

    def load_model(self, model: str) -> None:
        response = httpx.post(
            f"{self.provider.base_url}/v1/models/{quote(model, safe='')}/load",
            headers=self.provider.headers,
            timeout=None,
        )
        response.raise_for_status()

    def unload_model(self, model: str) -> None:
        response = httpx.post(
            f"{self.provider.base_url}/v1/models/{quote(model, safe='')}/unload",
            headers=self.provider.headers,
            timeout=None,
        )
        response.raise_for_status()

    def _apply_thinking(self, body: dict[str, object], think: str | None) -> None:
        # omlx ignores `reasoning_effort`; thinking is gated by the chat
        # template's `enable_thinking` flag. A level enables, "none"
        # disables, None leaves the model's template default. Sent
        # regardless of `supports_thinking`, so "off" always takes.
        if think is None:
            return
        body["chat_template_kwargs"] = {"enable_thinking": think != "none"}

    def _fetch_context_size(self, model: str) -> int | None:
        for entry in self._status_models(timeout=1.5):
            if entry.get("id") == model and isinstance(entry.get("max_context_window"), int):
                return int(entry["max_context_window"])
        return None

    def _status_models(self, timeout: float) -> list[dict[str, Any]]:
        data = self._get_json("/v1/models/status", timeout=timeout)
        if isinstance(data, dict):
            models = data.get("models")
            if isinstance(models, list):
                return models
        return []


def _read_home_json(relative: str) -> dict[str, Any]:
    """A JSON object at `~/<relative>`, or {} on any failure."""
    try:
        parsed = json.loads((Path.home() / relative).read_text())
    except OSError, json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
