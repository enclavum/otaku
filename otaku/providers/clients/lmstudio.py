"""LM Studio: the registry, load and unload, sizes, windows and each
model's capabilities via its /api/v1/models surface; chat rides the
OpenAI protocol at /v1. Reasoning is the app's own per-model switch:
nothing on the wire reaches it, so no knob is sent and no effort is
reported as honoured, whatever the model could do.
"""

from typing import Any, ClassVar

from otaku.providers import http
from otaku.providers.clients import read_home_json
from otaku.providers.http import ASK_TIMEOUT, PROBE_TIMEOUT, positive_int
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion
from otaku.providers.openai.models import Capabilities, Listing, ModelInfo, ModelState, OpenAIModels
from otaku.settings.providers import ProviderConfig


class LmStudioModels(OpenAIModels):
    @property
    def can_manage(self) -> bool:
        return True

    def load(self, model: str) -> None:
        # Idempotent on purpose: LM Studio's /load is not — repeated
        # calls stack 'model:2', ':3', … instances.
        entry = self._entry_of(model, ASK_TIMEOUT)
        if entry is not None and entry.get("loaded_instances"):
            return
        http.post_json(
            f"{self._config.base_url}/api/v1/models/load",
            {"model": model},
            name=self._config.name,
            headers=self._auth.headers,
            timeout=None,
        )

    def unload(self, model: str) -> None:
        # /unload takes an instance id: every loaded instance of the
        # model is unloaded in turn.
        entry = self._entry_of(model, ASK_TIMEOUT)
        for instance in (entry.get("loaded_instances") or []) if entry is not None else []:
            instance_id = instance.get("id")
            if not isinstance(instance_id, str):
                continue
            http.post_json(
                f"{self._config.base_url}/api/v1/models/unload",
                {"instance_id": instance_id},
                name=self._config.name,
                headers=self._auth.headers,
                timeout=None,
            )

    # ---------- the hooks ----------

    def _list(self, timeout: float) -> Listing:
        """Everything from the one /api/v1/models pass: key, size,
        capabilities, the model's maximum, and a loaded instance's
        configured window. No such surface — not LM Studio — falls to
        the plain names; a dead server raises out of them."""
        entries = self._registry(timeout)
        if not entries:
            return super()._list(timeout)
        models = []
        for entry in entries:
            key = entry.get("key")
            if not isinstance(key, str):
                continue
            window = _loaded_window_of(entry)
            models.append(
                ModelInfo(
                    name=key,
                    size=positive_int(entry.get("size_bytes")),
                    max_context_catalogue=positive_int(entry.get("max_context_length")),
                    max_context_loaded=window,
                    capabilities=_capabilities_of(entry),
                    state=ModelState.LOADED
                    if entry.get("loaded_instances")
                    else ModelState.UNLOADED,
                    checked=True,
                )
            )
        return sorted(models, key=lambda model: model.name)

    def _state(self, name: str) -> tuple[ModelState, int | None] | None:
        entry = self._entry_of(name, PROBE_TIMEOUT)
        if entry is None:
            return None
        if entry.get("loaded_instances"):
            return ModelState.LOADED, _loaded_window_of(entry)
        return ModelState.UNLOADED, None

    # ---------- the native surface ----------

    def _entry_of(self, model: str, timeout: float) -> dict[str, Any] | None:
        return next((e for e in self._registry(timeout) or [] if e.get("key") == model), None)

    def _registry(self, timeout: float) -> list[dict[str, Any]] | None:
        """The registry's entries; None when the server did not answer,
        or has no such surface."""
        data = http.get_json(
            f"{self._config.base_url}/api/v1/models",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
            quiet=True,
        )
        models = data.get("models") if isinstance(data, dict) else None
        return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else None


class LmStudioCompletion(OpenAICompletion):
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset()


class LmStudioClient(OpenAIClient):
    id = "lmstudio"
    label = "LM Studio"
    locality = Locality.LOCAL
    env_key = "LMSTUDIO_API_KEY"
    models_class = LmStudioModels
    completion_class = LmStudioCompletion

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The first-run section, its port from LM Studio's own server
        config file."""
        port = read_home_json(".lmstudio/.internal/http-server-config.json").get("port")
        url = f"http://localhost:{port if isinstance(port, int) else 1234}/v1"
        return ProviderConfig(name=cls.id, url=url)


def _capabilities_of(entry: dict[str, Any]) -> Capabilities:
    """What a registry entry says: vision from its capabilities; no
    effort reaches the model (the app's switch); the raw wire is there;
    decoding is constrained server-side (`response_format`)."""
    caps = entry.get("capabilities")
    vision = bool(caps["vision"]) if isinstance(caps, dict) and "vision" in caps else None
    return Capabilities(
        vision=vision, reasoning=frozenset(), text_completion=True, structured_output=True
    )


def _loaded_window_of(entry: dict[str, Any]) -> int | None:
    """The window a loaded instance was configured with; None without
    an instance — the model's maximum is not what an instance gets."""
    for instance in entry.get("loaded_instances") or []:
        config = instance.get("config") or {}
        window = positive_int(config.get("context_length"))
        if window:
            return window
    return None
