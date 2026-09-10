"""LM Studio: the model registry, load/unload, sizes, context windows
and each model's capabilities via its /api/v1/models surface; chat
rides the OpenAI protocol at /v1. Thinking is the app's own per-model
switch: nothing on the wire reaches it, so no knob is sent and no level
is reported as honoured, whatever the model could do.
"""

from typing import Any, ClassVar

from otaku.providers import http
from otaku.providers.client import (
    ASK_TIMEOUT,
    PROBE_TIMEOUT,
    Capabilities,
    Client,
    Locality,
    ModelInfo,
)
from otaku.providers.clients import read_home_json
from otaku.providers.wire import positive_int
from otaku.settings.providers import ProviderConfig


class LmStudioClient(Client):
    kind = "lmstudio"
    label = "LM Studio"
    locality = Locality.LOCAL
    env_key = "LMSTUDIO_API_KEY"
    thinking_knobs: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The first-run section, its port detected from LM Studio's own
        server config file."""
        port = read_home_json(".lmstudio/.internal/http-server-config.json").get("port")
        url = f"http://localhost:{port if isinstance(port, int) else 1234}/v1"
        return ProviderConfig(name=cls.kind, url=url)

    @property
    def manages_models(self) -> bool:
        return True

    def load_model(self, model: str) -> None:
        # Idempotent on purpose: LM Studio's /load is not — repeated calls
        # stack 'model:2', ':3', … instances. Skip when already loaded.
        if any(row.name == model and row.loaded for row in self.models()):
            return
        http.post_json(
            f"{self.config.base_url}/api/v1/models/load",
            {"model": model},
            name=self.config.name,
            headers=self._headers,
            timeout=None,
        )

    def unload_model(self, model: str) -> None:
        # /unload takes an instance id: sweep every loaded instance whose
        # key matches `model` and unload each.
        entry = self._entry_of(model, timeout=ASK_TIMEOUT)
        for instance in (entry.get("loaded_instances") or []) if entry is not None else []:
            instance_id = instance.get("id")
            if not isinstance(instance_id, str):
                continue
            http.post_json(
                f"{self.config.base_url}/api/v1/models/unload",
                {"instance_id": instance_id},
                name=self.config.name,
                headers=self._headers,
                timeout=None,
            )

    def models(self, timeout: float = ASK_TIMEOUT) -> list[ModelInfo]:
        """One row per model, everything from the one /api/v1/models pass:
        key, size, loaded instances, and the context window (a loaded
        instance's configured window beats the model's maximum). No such
        surface — not LM Studio — falls to the plain names; a dead
        server raises out of them."""
        entries = self._registry(timeout)
        if not entries:
            return super().models(timeout)
        rows = []
        for entry in entries:
            key = entry.get("key")
            if not isinstance(key, str):
                continue
            caps = entry.get("capabilities")
            vision = bool(caps.get("vision", True)) if isinstance(caps, dict) else True
            rows.append(
                ModelInfo(
                    name=key,
                    capabilities=Capabilities(vision=vision, thinking=frozenset()),
                    size=positive_int(entry.get("size_bytes")),
                    context=_context_size_of(entry),
                    loaded=bool(entry.get("loaded_instances")),
                )
            )
        return sorted(rows, key=lambda row: row.name)

    def _context_size(self, model: str) -> int | None:
        entry = self._entry_of(model, timeout=PROBE_TIMEOUT)
        return _context_size_of(entry) if entry is not None else None

    def _entry_of(self, model: str, timeout: float) -> dict[str, Any] | None:
        return next((e for e in self._registry(timeout) if e.get("key") == model), None)

    def _registry(self, timeout: float) -> list[dict[str, Any]]:
        data = http.get_json(
            f"{self.config.base_url}/api/v1/models",
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
            quiet=True,
        )
        models = data.get("models") if isinstance(data, dict) else None
        return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else []


def _context_size_of(entry: dict[str, Any]) -> int | None:
    """The context window of one registry entry: a loaded instance's
    configured length when there is one, the model's maximum otherwise."""
    for instance in entry.get("loaded_instances") or []:
        config = instance.get("config") or {}
        context = positive_int(config.get("context_length"))
        if context:
            return context
    return positive_int(entry.get("max_context_length"))
