"""llama.cpp's server (`llama-server`), in either of its two modes: one
model chosen at launch, or the router (`--models-dir`), which fronts a
folder of models and loads and unloads them by name. Chat rides the
OpenAI protocol at /v1. The listing carries what a model half needs
— the loaded context size, the model's ceiling and its size in `meta`, and
in router mode the modalities of every entry — so `/props` is read
once per listing, for the modalities in single mode, and nothing is
read for a single server's state, which never changes. A router's
entries carry a `status`, which is how the mode is told: `can_manage`
is settled by the first listing and false until then. In router mode
any request for an unloaded model loads it, so the counts ask not to.
`/v1/models` is exempt from the server's key check and `/props` is
not, so with a key configured that one read is also the key check —
or a wrong key would show only at the first turn.
"""

import time
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from otaku.providers import http
from otaku.providers.clients import launched_port
from otaku.providers.errors import ProviderError, StatusError, UnreachableError
from otaku.providers.http import ASK_TIMEOUT, PROBE_TIMEOUT, positive_int
from otaku.providers.openai import reasoning, requests
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion
from otaku.providers.openai.models import Capabilities, Listing, ModelInfo, ModelState, OpenAIModels
from otaku.providers.openai.requests import WireMessage
from otaku.settings.providers import ProviderConfig

_LOAD_POLL_SECONDS = 0.5  # how often a router is asked whether an order is done
_SILENCE_BUDGET = 10.0  # how long a router may leave polls unanswered before the order is lost
# A router entry's words for a model with a server behind it: loaded, or
# put to sleep idle (`--sleep-idle-seconds`) and woken by the next request.
_RUNNING = frozenset({"loaded", "sleeping"})


class LlamaCppModels(OpenAIModels):
    """The reads both modes share: the listing, which also tells the
    mode — a router's entries carry a `status` — and `/props`. Every
    listing swaps the instance to the mode's own class, so a server
    restarted the other way is followed; the cache and the lock stay.
    A fresh client is a single server until a listing says otherwise."""

    def _list(self, timeout: float) -> Listing:
        data = http.get_json(
            f"{self._config.url}/models",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
        )
        # With a key the props read is loud, so a wrong key raises here;
        # without one, a server that lacks /props is a server without
        # modalities.
        props = http.get_json(
            f"{self._config.base_url}/props",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
            quiet=not self._auth.api_key,
        )
        entries = self._entries_of(data)
        is_router = any("status" in entry for entry in entries)
        self.__class__ = LlamaCppRouterModels if is_router else LlamaCppSingleModels
        return self._listing(entries, props)

    def _listing(self, entries: list[dict[str, Any]], props: Any) -> Listing:
        """The entries as the mode reads them — each mode's own."""
        raise NotImplementedError

    def _entries_of(self, data: object) -> list[dict[str, Any]]:
        raw = data.get("data") if isinstance(data, dict) else None
        return [e for e in raw or [] if isinstance(e, dict) and isinstance(e.get("id"), str)]

    def _meta_of(self, entry: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
        """(the loaded context size, the model's ceiling, the size on
        disk) from an entry's `meta`: `n_ctx` is per slot, what one
        request gets."""
        meta = entry.get("meta")
        if not isinstance(meta, dict):
            return None, None, None
        return (
            positive_int(meta.get("n_ctx")),
            positive_int(meta.get("n_ctx_train")),
            positive_int(meta.get("size")),
        )

    def _capabilities_of(self, modalities: object) -> Capabilities:
        """What the modalities say — a router entry's `input_modalities`
        list, or a props object's `modalities` flags; neither leaves
        vision and audio unknown. The raw wire is always there and
        decoding is constrained server-side; which efforts the template
        grades, the server cannot say."""
        vision: bool | None
        audio: bool | None
        if isinstance(modalities, list):
            vision, audio = "image" in modalities, "audio" in modalities
        elif isinstance(modalities, dict):
            vision = bool(modalities.get("vision")) if "vision" in modalities else None
            audio = bool(modalities.get("audio")) if "audio" in modalities else None
        else:
            vision = audio = None
        return Capabilities(
            vision=vision, audio=audio, text_completion=True, structured_output=True
        )


class LlamaCppSingleModels(LlamaCppModels):
    """One model, loaded at launch, its state never changing: the
    listing's one entry carries its loaded context size, ceiling and
    size, `/props` its modalities, and every listed name is that
    model's. Nothing is asked live, nothing is managed."""

    def _listing(self, entries: list[dict[str, Any]], props: Any) -> Listing:
        if not entries:
            return []
        max_context_loaded, ceiling, size = self._meta_of(entries[0])
        # Props without `modalities` are a build older than mid-2025:
        # still llama.cpp, vision and audio unknown. No props, nothing
        # is known.
        answered = isinstance(props, dict)
        modalities = props.get("modalities") if answered else None
        return [
            ModelInfo(
                name=str(entry["id"]),
                size=size,
                max_context_catalogue=ceiling,
                max_context_loaded=max_context_loaded,
                capabilities=self._capabilities_of(modalities) if answered else None,
                state=ModelState.LOADED,
                checked=answered,
            )
            for entry in sorted(entries, key=lambda entry: str(entry["id"]))
        ]


class LlamaCppRouterModels(LlamaCppModels):
    """A folder of models, each with a server of its own that the router
    starts on demand — on any request for it, so nothing here asks for
    a model the router does not say is running. An order is taken at
    once and carried out after: a load or unload waits on the entry's
    status, and a poll the router leaves unanswered is waited out."""

    @property
    def can_manage(self) -> bool:
        return True

    def load(self, model: str) -> None:
        status = self._status_of(self._entry(model) or {})
        if status in _RUNNING:
            return
        # A load already in flight — the router autoloads on any request
        # for the model — is one to wait for, not to order again; the
        # router's own word for that, when the probe missed it, is the
        # 400 "already running".
        if status != "loading":
            try:
                http.post_json(
                    f"{self._config.base_url}/models/load",
                    {"model": model},
                    name=self._config.name,
                    headers=self._auth.headers,
                    timeout=None,
                )
            except StatusError as e:
                if e.status != 400:
                    raise
        entry = self._wait(model, lambda status: status == "loading")
        if self._status_of(entry) not in _RUNNING:
            status_object = entry.get("status")
            code = status_object.get("exit_code") if isinstance(status_object, dict) else None
            suffix = f" (exit code {code})" if isinstance(code, int) else ""
            raise ProviderError(f"{model} did not load on {self._config.name}{suffix}.")

    def unload(self, model: str) -> None:
        status = self._status_of(self._entry(model) or {})
        if status not in _RUNNING and status != "loading":
            return  # nothing running to stop
        http.post_json(
            f"{self._config.base_url}/models/unload",
            {"model": model},
            name=self._config.name,
            headers=self._auth.headers,
            timeout=None,
        )
        self._wait(model, lambda status: status in _RUNNING or status == "loading")

    # ---------- the hooks ----------

    def _listing(self, entries: list[dict[str, Any]], props: Any) -> Listing:
        # Every entry states its modalities; the loaded context size,
        # ceiling and size are merged in for a running one.
        models = []
        for entry in entries:
            max_context_loaded, ceiling, size = self._meta_of(entry)
            architecture = entry.get("architecture")
            modalities = (
                architecture.get("input_modalities") if isinstance(architecture, dict) else None
            )
            models.append(
                ModelInfo(
                    name=str(entry["id"]),
                    size=size,
                    max_context_catalogue=ceiling,
                    max_context_loaded=max_context_loaded,
                    capabilities=self._capabilities_of(modalities),
                    state=self._state_of(entry),
                    checked=True,
                )
            )
        return sorted(models, key=lambda model: model.name)

    def _state(self, name: str) -> tuple[ModelState, int | None] | None:
        entry = self._entry(name)
        if entry is None:
            return None
        return self._state_of(entry), self._meta_of(entry)[0]

    # ---------- the router's own ----------

    def _entry(self, model: str) -> dict[str, Any] | None:
        """The model's entry in the listing now, as a probe: {} when the
        router lists no such model, None when it did not answer — which
        the polling tells apart."""
        data = http.get_json(
            f"{self._config.url}/models",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        if not isinstance(data, dict):
            return None
        return next((e for e in self._entries_of(data) if e.get("id") == model), {})

    def _wait(self, model: str, pending: Callable[[str | None], bool]) -> dict[str, Any]:
        """The model's entry once its status is no longer `pending`. A
        poll the router does not answer — it holds its lock while a
        child spawns — is waited out, up to a budget of silence."""
        silence = 0.0
        while True:
            entry = self._entry(model)
            if entry is None:
                silence += _LOAD_POLL_SECONDS
                if silence > _SILENCE_BUDGET:
                    raise UnreachableError(f"{self._config.name} stopped answering.")
            elif not pending(self._status_of(entry)):
                return entry
            else:
                silence = 0.0
            time.sleep(_LOAD_POLL_SECONDS)

    def _status_of(self, entry: dict[str, Any]) -> str | None:
        """An entry's status word, `status.value`; None for a model the
        router does not list."""
        status = entry.get("status")
        value = status.get("value") if isinstance(status, dict) else None
        return value if isinstance(value, str) else None

    def _state_of(self, entry: dict[str, Any]) -> ModelState:
        """The status word as a state; a model the router does not list
        is unloaded, since it lists every model it fronts."""
        status = self._status_of(entry)
        if status in _RUNNING:
            return ModelState.LOADED
        if status == "loading":
            return ModelState.LOADING
        return ModelState.UNLOADED


class LlamaCppCompletion(OpenAICompletion):
    # The template's flag is what stops Gemma 4 and its kind; the effort
    # rides beside it as a template variable, for the templates that
    # read one. A request-level reasoning_effort is not read.
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {reasoning.FLAG_KNOB, reasoning.TEMPLATE_EFFORT_KNOB}
    )
    can_count_tokens = True

    def count_chat_tokens(
        self, model: str, messages: Sequence[WireMessage], timeout: float = ASK_TIMEOUT
    ) -> int | None:
        # The same body a turn would send, streaming fields aside, so the
        # count is of what the template renders for it. A router must
        # not load the model for a count.
        body = requests.chat_completion_body(model, messages, {})
        request = {k: v for k, v in body.items() if k not in ("stream", "stream_options")}
        data = http.post_json(
            f"{self._config.url}/chat/completions/input_tokens?autoload=false",
            request,
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
            quiet=True,
        )
        count = data.get("input_tokens") if isinstance(data, dict) else None
        return count if isinstance(count, int) else None

    def count_text_tokens(
        self, model: str, prompt: str, timeout: float = ASK_TIMEOUT
    ) -> int | None:
        data = http.post_json(
            f"{self._config.base_url}/tokenize?autoload=false",
            # The model rides in the body, which is how a router forwards
            # a POST; a single server ignores it. `add_special` adds the
            # BOS the text wire counts and /tokenize leaves out.
            {"model": model, "content": prompt, "add_special": True},
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
            quiet=True,
        )
        tokens = data.get("tokens") if isinstance(data, dict) else None
        return len(tokens) if isinstance(tokens, list) else None

    def _convert_params(self, params: dict[str, object]) -> dict[str, object]:
        # `repetition_penalty` under the name the server reads.
        if "repetition_penalty" not in params:
            return params
        spelled = dict(params)
        spelled["repeat_penalty"] = spelled.pop("repetition_penalty")
        return spelled


class LlamaCppClient(OpenAIClient):
    id = "llamacpp"
    label = "llama.cpp"
    locality = Locality.LOCAL
    env_key = "LLAMACPP_API_KEY"
    models_class = LlamaCppSingleModels
    completion_class = LlamaCppCompletion

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # Configured by launch flags, nothing on disk: a running server's
        # own flag is read, else the standard default. The server is
        # `llama-server`, or the unified binary's `llama serve`.
        port = launched_port("llama-server") or launched_port("llama serve") or 8080
        return ProviderConfig(name=cls.id, url=f"http://localhost:{port}/v1")
