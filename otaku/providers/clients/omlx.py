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

from otaku.providers.clients import read_home_json
from otaku.providers.errors import StatusError
from otaku.providers.http import ASK_TIMEOUT, PROBE_TIMEOUT, Http, positive_int
from otaku.providers.openai import reasoning
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import PROTOCOL_PARAMS, SAMPLER_PARAMS, OpenAICompletion
from otaku.providers.openai.models import Capabilities, Listing, ModelInfo, ModelState, OpenAIModels
from otaku.providers.openai.requests import Image, WireMessage
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

    def _list(self, http: Http) -> Listing:
        """Everything from the one /v1/models/status pass; a model the
        operator hid, or a speculative drafter, is not offered. Loud: a
        server still starting (503), or refusing the key, is the
        listing's own sentence. A 404 alone — no status surface, not an
        omlx server — falls to the plain names."""
        try:
            data = http.get(f"{self._config.base_url}/v1/models/status")
        except StatusError as e:
            if e.status != 404:
                raise
            return super()._list(http)
        models = []
        for entry in _entries(data):
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
                    size=positive_int(entry.get("estimated_size")),  # the weights, estimated
                    max_context_catalogue=positive_int(entry.get("model_context_length")),
                    max_context_loaded=max_context_loaded,
                    capabilities=self._capabilities_of(entry),
                    state=state,
                )
            )
        return sorted(models, key=lambda model: model.name)

    def _get(self, name: str, http: Http) -> ModelInfo | None:
        entry = _status_entry(http, self._config.base_url, name)
        if entry is None:
            return None
        state, max_context_loaded = self._state_of(entry)
        return ModelInfo(name=name, max_context_loaded=max_context_loaded, state=state)

    # ---------- the native surface ----------

    def _model_action(self, action: str, model: str) -> None:
        # omlx evicts on its own (idle, LRU, memory pressure); an unload
        # of what is not loaded is a 400, a load of what is a no-op: ask
        # first, order only what is not so.
        entry = _status_entry(self._http, self._config.base_url, model)
        if entry is not None and bool(entry.get("loaded")) == (action == "load"):
            return
        self._http.post(
            f"{self._config.base_url}/v1/models/{quote(model, safe='')}/{action}",
            {},
            purpose=action,
        )

    def _state_of(self, entry: dict[str, Any]) -> tuple[ModelState, int | None]:
        """The instance's facts off a status entry: the state, and the
        context size a request gets — `max_context_window`, the serving
        cap, never the model's own length. The cap is config, stated in
        every state, and omlx loads on demand under it: an unloaded
        model's budget is the cap, not a default."""
        if entry.get("loaded"):
            state = ModelState.LOADED
        elif entry.get("is_loading"):
            state = ModelState.LOADING
        else:
            state = ModelState.UNLOADED
        return state, positive_int(entry.get("max_context_window"))

    def _capabilities_of(self, entry: dict[str, Any]) -> Capabilities:
        """Only a VLM takes images, and a type the status does not state
        leaves the question open. `thinking_default` is whether the
        template has the thinking toggle at all: None, and no effort
        reaches the model; a bool, and every effort does, as on or off.
        Decoding is held to a schema server-side (a grammar compiled
        for `response_format`) for every model."""
        kind = entry.get("model_type")
        vision = kind == "vlm" if isinstance(kind, str) and kind else None
        toggle = entry.get("thinking_default")
        return Capabilities(
            vision=vision,
            reasoning=reasoning.ALL_EFFORTS if isinstance(toggle, bool) else frozenset(),
            text_completion=True,
            structured_output=True,
        )


class OmlxCompletion(OpenAICompletion):
    supported_params = PROTOCOL_PARAMS | SAMPLER_PARAMS
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {reasoning.FLAG_KNOB, reasoning.TEMPLATE_EFFORT_KNOB}
    )
    can_count_tokens = True

    def count_chat_tokens(
        self,
        model: str,
        messages: Sequence[WireMessage],
        *,
        effort: str | None = None,
        images: Sequence[Image] = (),
        timeout: float = ASK_TIMEOUT,
    ) -> int | None:
        # The count resolves the engine, which loads the model; a count
        # is never worth a load. Anthropic's shape takes neither the
        # template kwargs nor an image, so `effort` and `images` do not
        # reach it: the count is of the template's default rendering.
        entry = _status_entry(self._http, self._config.base_url, model)
        if entry is None or not entry.get("loaded"):
            return None
        # Anthropic's shape: the system text apart, the turns as messages.
        system = "\n\n".join(m.body for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": m.body} for m in messages if m.role != "system"]
        body: dict[str, Any] = {"model": model, "messages": turns}
        if system:
            body["system"] = system
        data = self._http.post(
            f"{self._config.base_url}/v1/messages/count_tokens", body, timeout=timeout, quiet=True
        )
        return positive_int(data.get("input_tokens")) if isinstance(data, dict) else None


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
        port = positive_int(server.get("port")) if isinstance(server, dict) else None
        auth = settings.get("auth")
        key = auth.get("api_key") if isinstance(auth, dict) else None
        url = f"http://localhost:{port or 8000}/v1"
        return ProviderConfig(name=cls.id, url=url, api_key=str(key) if key else "")


def _status_entry(http: Http, base_url: str, model: str) -> dict[str, Any] | None:
    """The model's entry in the status listing, off a quiet probe: None
    when the server did not answer, or does not list it. The instance
    reads' door, shared by the two halves, so `http` is whichever is
    asking: the model's view, or the transport."""
    data = http.get(f"{base_url}/v1/models/status", timeout=PROBE_TIMEOUT, quiet=True)
    return next((e for e in _entries(data) if e.get("id") == model), None)


def _entries(data: Any) -> list[dict[str, Any]]:
    """The status listing's entries off its answer; [] for any other shape."""
    models = data.get("models") if isinstance(data, dict) else None
    return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else []
