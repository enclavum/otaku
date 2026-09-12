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
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion
from otaku.providers.openai.models import Capabilities, Listing, ModelInfo, ModelState, OpenAIModels
from otaku.providers.openai.requests import WireMessage
from otaku.settings.providers import ProviderConfig

# The types a chat can be had with; the embedding, reranker and audio
# kinds answer a chat with a 400, and the markitdown pseudo-model with a
# document conversion.
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
        """Everything from the one /v1/models/status pass; a model the
        operator hid, or a speculative drafter, is not offered. No status
        surface — not an omlx server — falls to the plain names; a dead
        server raises out of them."""
        entries = _status(self._config, self._auth, timeout)
        if not entries:
            return super()._list(timeout)
        models = []
        for entry in entries:
            model_id = entry.get("id")
            if not isinstance(model_id, str) or entry.get("is_hidden") or entry.get("is_helper"):
                continue
            kind = entry.get("model_type")
            if isinstance(kind, str) and kind and kind not in _LANGUAGE_MODELS:
                continue
            state, max_context_loaded = self._state_of(entry)
            models.append(
                ModelInfo(
                    name=model_id,
                    size=positive_int(entry.get("estimated_size")),  # the weights on disk
                    max_context_catalogue=positive_int(entry.get("model_context_length")),
                    max_context_loaded=max_context_loaded,
                    capabilities=self._capabilities_of(entry),
                    state=state,
                    checked=True,
                )
            )
        return sorted(models, key=lambda model: model.name)

    def _state(self, name: str) -> tuple[ModelState, int | None] | None:
        entry = _status_entry(self._config, self._auth, name)
        return self._state_of(entry) if entry is not None else None

    # ---------- the native surface ----------

    def _model_action(self, action: str, model: str) -> None:
        # omlx evicts on its own (idle, LRU, memory pressure); an unload
        # of what is not loaded is a 400, a load of what is a no-op: ask
        # first, order only what is not so.
        entry = _status_entry(self._config, self._auth, model)
        if entry is not None and bool(entry.get("loaded")) == (action == "load"):
            return
        http.post_json(
            f"{self._config.base_url}/v1/models/{quote(model, safe='')}/{action}",
            {},
            name=self._config.name,
            headers=self._auth.headers,
            timeout=None,
        )

    def _state_of(self, entry: dict[str, Any]) -> tuple[ModelState, int | None]:
        """The instance's facts off a status entry: loaded, with the
        context size it serves — `max_context_window`, the serving cap,
        never the model's own length — or loading, or unloaded."""
        if entry.get("loaded"):
            return ModelState.LOADED, positive_int(entry.get("max_context_window"))
        if entry.get("is_loading"):
            return ModelState.LOADING, None
        return ModelState.UNLOADED, None

    def _capabilities_of(self, entry: dict[str, Any]) -> Capabilities:
        """Only a VLM takes images, and a type the status does not state
        leaves the question open. `thinking_default` is whether the
        template has the thinking toggle at all: None, and no effort
        reaches the model; a bool, and every effort does, as on or off."""
        kind = entry.get("model_type")
        vision = kind == "vlm" if isinstance(kind, str) and kind else None
        toggle = entry.get("thinking_default")
        return Capabilities(
            vision=vision,
            reasoning=reasoning.ALL_EFFORTS if isinstance(toggle, bool) else frozenset(),
            text_completion=True,
        )


class OmlxCompletion(OpenAICompletion):
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {reasoning.FLAG_KNOB, reasoning.TEMPLATE_EFFORT_KNOB}
    )
    can_count_tokens = True

    def count_chat_tokens(
        self, model: str, messages: Sequence[WireMessage], timeout: float = ASK_TIMEOUT
    ) -> int | None:
        # The count resolves the engine, which loads the model; a count
        # is never worth a load.
        entry = _status_entry(self._config, self._auth, model)
        if entry is None or not entry.get("loaded"):
            return None
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


def _status(
    config: ProviderConfig, auth: OpenAIAuth, timeout: float
) -> list[dict[str, Any]] | None:
    """The status listing's entries; None when the server did not answer,
    or has no such surface."""
    data = http.get_json(
        f"{config.base_url}/v1/models/status",
        name=config.name,
        headers=auth.headers,
        timeout=timeout,
        quiet=True,
    )
    models = data.get("models") if isinstance(data, dict) else None
    return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else None


def _status_entry(config: ProviderConfig, auth: OpenAIAuth, model: str) -> dict[str, Any] | None:
    return next(
        (e for e in _status(config, auth, PROBE_TIMEOUT) or [] if e.get("id") == model), None
    )
