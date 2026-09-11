"""omlx: an MLX model server speaking the OpenAI protocol for chat,
with its own registry and load/unload surface under /v1/models, each
model's type (a VLM takes images) in the status listing, and
Anthropic's token count at /v1/messages/count_tokens. A reasoning effort
rides in `chat_template_kwargs` alone, forwarded into the template
verbatim: the flag gates reasoning, the effort is a template variable.
"""

from collections.abc import Sequence
from typing import Any, ClassVar
from urllib.parse import quote

from otaku.providers import http
from otaku.providers.clients import read_home_json
from otaku.providers.http import ASK_TIMEOUT, PROBE_TIMEOUT, positive_int
from otaku.providers.openai import reasoning
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion
from otaku.providers.openai.models import Capabilities, Listing, ModelInfo, ModelState, OpenAIModels
from otaku.providers.openai.requests import WireMessage
from otaku.settings.providers import ProviderConfig

# The types a chat can be had with; the rest (embedding, reranker, the
# audio kinds, the markitdown pseudo-model) answer a chat with a 400.
_LANGUAGE_MODELS = frozenset({"llm", "vlm"})


class OmlxModels(OpenAIModels):
    @property
    def can_manage(self) -> bool:
        return True

    def load(self, model: str) -> None:
        self._model_action("load", model)

    def unload(self, model: str) -> None:
        self._model_action("unload", model)

    # ---------- the hooks ----------

    def _list(self, timeout: float) -> Listing:
        """Everything from the one /v1/models/status pass: id, type, size,
        state, and the model's maximum window, which a loaded instance
        serves whole. No status surface — not an omlx server — falls to
        the plain names; a dead server raises out of them."""
        entries = self._status(timeout)
        if not entries:
            return super()._list(timeout)
        models = []
        for entry in entries:
            model_id = entry.get("id")
            if not isinstance(model_id, str):
                continue
            kind = entry.get("model_type")
            if isinstance(kind, str) and kind and kind not in _LANGUAGE_MODELS:
                continue
            # Only a VLM takes images; a type the status does not state
            # leaves the question open.
            vision = kind == "vlm" if isinstance(kind, str) and kind else None
            window = positive_int(entry.get("max_context_window"))
            loaded = bool(entry.get("loaded"))
            models.append(
                ModelInfo(
                    name=model_id,
                    size=positive_int(entry.get("actual_size") or entry.get("estimated_size")),
                    max_context_catalogue=window,
                    max_context_loaded=window if loaded else None,
                    capabilities=Capabilities(vision=vision, text_completion=True),
                    state=ModelState.LOADED if loaded else ModelState.UNLOADED,
                    checked=True,
                )
            )
        return sorted(models, key=lambda model: model.name)

    def _state(self, name: str) -> tuple[ModelState, int | None] | None:
        entry = self._status_entry(name)
        if entry is None:
            return None
        if bool(entry.get("loaded")):
            return ModelState.LOADED, positive_int(entry.get("max_context_window"))
        return ModelState.UNLOADED, None

    # ---------- the native surface ----------

    def _model_action(self, action: str, model: str) -> None:
        # omlx evicts on its own (idle, LRU, memory pressure) and refuses
        # an order its state already satisfies: ask first, order only
        # what is not so.
        entry = self._status_entry(model)
        if entry is not None and bool(entry.get("loaded")) == (action == "load"):
            return
        http.post_json(
            f"{self._config.base_url}/v1/models/{quote(model, safe='')}/{action}",
            {},
            name=self._config.name,
            headers=self._auth.headers,
            timeout=None,
        )

    def _status_entry(self, model: str) -> dict[str, Any] | None:
        return next((e for e in self._status(PROBE_TIMEOUT) or [] if e.get("id") == model), None)

    def _status(self, timeout: float) -> list[dict[str, Any]] | None:
        """The status listing's entries; None when the server did not
        answer, or has no such surface."""
        data = http.get_json(
            f"{self._config.base_url}/v1/models/status",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
            quiet=True,
        )
        models = data.get("models") if isinstance(data, dict) else None
        return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else None


class OmlxCompletion(OpenAICompletion):
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {reasoning.FLAG_KNOB, reasoning.TEMPLATE_EFFORT_KNOB}
    )
    can_count_tokens = True

    def count_chat_tokens(
        self, model: str, messages: Sequence[WireMessage], timeout: float = ASK_TIMEOUT
    ) -> int | None:
        # Anthropic's shape: the system text apart, the turns as messages.
        system = "\n\n".join(m.body for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": m.body} for m in messages if m.role != "system"]
        body: dict[str, Any] = {"model": model, "messages": turns}
        if system:
            body["system"] = system
        data = http.post_json(
            f"{self._config.base_url}/v1/messages/count_tokens",
            body,
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
            quiet=True,
        )
        count = data.get("input_tokens") if isinstance(data, dict) else None
        return count if isinstance(count, int) else None


class OmlxClient(OpenAIClient):
    id = "omlx"
    label = "oMLX"
    locality = Locality.LOCAL
    env_key = "OMLX_API_KEY"
    models_class = OmlxModels
    completion_class = OmlxCompletion

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The first-run section, its port and api key from omlx's own
        settings file."""
        settings = read_home_json(".omlx/settings.json")
        server = settings.get("server")
        port = server.get("port") if isinstance(server, dict) else None
        auth = settings.get("auth")
        key = auth.get("api_key") if isinstance(auth, dict) else None
        url = f"http://localhost:{port if isinstance(port, int) else 8000}/v1"
        return ProviderConfig(name=cls.id, url=url, api_key=str(key) if key else "")
