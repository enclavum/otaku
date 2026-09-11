"""llama.cpp's server (`llama-server`), in either of its two modes: one
model chosen at launch, or the router (`--models-dir`), which fronts a
folder of models and loads and unloads them by name. Chat rides the
OpenAI protocol at /v1; the native surface adds `/props` (the loaded
window and the modalities), `/tokenize`, the chat request's token
count, and the router's `/models` family. A reasoning effort rides in
`chat_template_kwargs` alone: the flag gates reasoning, the effort is a
template variable. A router's listing entries carry a `status`, which
is how the mode is told: `can_manage` is settled by the first listing
and false until then.
"""

import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, ClassVar
from urllib.parse import quote

from otaku.providers import http
from otaku.providers.clients import launched_port
from otaku.providers.errors import ProviderError
from otaku.providers.http import ASK_TIMEOUT, PROBE_TIMEOUT, positive_int
from otaku.providers.openai import reasoning, requests
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion
from otaku.providers.openai.models import Capabilities, Listing, ModelInfo, ModelState, OpenAIModels
from otaku.providers.openai.requests import WireMessage
from otaku.settings.providers import ProviderConfig

_LOAD_POLL_SECONDS = 0.5  # how often a router is asked whether a load is done
# A router entry's words for a model with a server behind it: loaded, or
# put to sleep idle (`--sleep-idle-seconds`) and woken by the next request.
_RUNNING = frozenset({"loaded", "sleeping"})


class LlamaCppModels(OpenAIModels):
    """One class facing the client, one of two behind it: every listing
    tells the mode and swaps the one in force, so a server restarted
    the other way is followed."""

    def __init__(self, config: ProviderConfig, auth: OpenAIAuth) -> None:
        super().__init__(config, auth)
        self._mode: LlamaCppSingleModels | LlamaCppRouterModels = LlamaCppSingleModels(config, auth)

    @property
    def can_manage(self) -> bool:
        return isinstance(self._mode, LlamaCppRouterModels)

    def load(self, model: str) -> None:
        if isinstance(self._mode, LlamaCppRouterModels):
            return self._mode.load(model)
        super().load(model)

    def unload(self, model: str) -> None:
        if isinstance(self._mode, LlamaCppRouterModels):
            return self._mode.unload(model)
        super().unload(model)

    # ---------- the hooks ----------

    def _list(self, timeout: float) -> Listing:
        data = http.get_json(
            f"{self._config.url}/models",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
        )
        raw = data.get("data") if isinstance(data, dict) else None
        entries = [e for e in raw or [] if isinstance(e, dict) and isinstance(e.get("id"), str)]
        is_router = any("status" in entry for entry in entries)
        if is_router != isinstance(self._mode, LlamaCppRouterModels):
            mode = LlamaCppRouterModels if is_router else LlamaCppSingleModels
            self._mode = mode(self._config, self._auth)
        return self._mode.list(entries, timeout)

    def _enhance(self, model: ModelInfo, timeout: float) -> ModelInfo:
        return self._mode.enhance(model)

    def _state(self, name: str) -> tuple[ModelState, int | None] | None:
        return self._mode.state(name)


class LlamaCppSingleModels:
    """One model, loaded at launch: the server's `/props` says everything,
    and one probe stamps every listed name."""

    def __init__(self, config: ProviderConfig, auth: OpenAIAuth) -> None:
        self._config = config
        self._auth = auth

    def list(self, entries: list[dict[str, Any]], timeout: float) -> Listing:
        names = sorted(str(entry["id"]) for entry in entries)
        props = self._props(timeout) if names else None
        listed = [ModelInfo(name=name, state=ModelState.LOADED) for name in names]
        return [_with_props(model, props) if props else model for model in listed]

    def enhance(self, model: ModelInfo) -> ModelInfo:
        props = self._props(PROBE_TIMEOUT)
        return _with_props(model, props) if props else model

    def state(self, name: str) -> tuple[ModelState, int | None] | None:
        props = self._props(PROBE_TIMEOUT)
        return (ModelState.LOADED, _window_of(props)) if props else None

    def _props(self, timeout: float) -> dict[str, Any] | None:
        return _get_props(self._config, self._auth, f"{self._config.base_url}/props", timeout)


class LlamaCppRouterModels:
    """A folder of models, each with a server of its own that the router
    starts on demand — on any request for it, `/props` included, so
    props are read only for a model the router says is running. An
    order is taken at once and carried out after: a load or unload
    waits on the entry's status."""

    def __init__(self, config: ProviderConfig, auth: OpenAIAuth) -> None:
        self._config = config
        self._auth = auth

    def list(self, entries: list[dict[str, Any]], timeout: float) -> Listing:
        models = []
        for entry in entries:
            name, state = str(entry["id"]), _state_of(entry)
            model = ModelInfo(name=name, state=state)
            props = self._props(name, timeout) if state is ModelState.LOADED else None
            models.append(_with_props(model, props) if props else model)
        return sorted(models, key=lambda model: model.name)

    def enhance(self, model: ModelInfo) -> ModelInfo:
        if _state_of(self._entry(model.name)) is not ModelState.LOADED:
            return model  # props would load it; asked again once it is
        props = self._props(model.name, PROBE_TIMEOUT)
        return _with_props(model, props) if props else model

    def state(self, name: str) -> tuple[ModelState, int | None] | None:
        entry = self._entry(name)
        if entry is None:
            return None
        state = _state_of(entry)
        props = self._props(name, PROBE_TIMEOUT) if state is ModelState.LOADED else None
        return state, _window_of(props)

    def load(self, model: str) -> None:
        self._action("load", model)

    def unload(self, model: str) -> None:
        self._action("unload", model)

    def _action(self, action: str, model: str) -> None:
        """An order the state already satisfies is nothing to do (the
        router would refuse it); a load already in flight is one to
        wait for, not to order again. A load that ends anything but
        running failed, and says so with the exit code the router kept."""
        status = _status_of(self._entry(model))
        if (status in _RUNNING) == (action == "load"):
            return
        if not (action == "load" and status == "loading"):
            http.post_json(
                f"{self._config.base_url}/models/{action}",
                {"model": model},
                name=self._config.name,
                headers=self._auth.headers,
                timeout=None,
            )
        if action == "load":
            while _status_of(entry := self._entry(model)) == "loading":
                time.sleep(_LOAD_POLL_SECONDS)
            if _status_of(entry) not in _RUNNING:
                code = _exit_code_of(entry)
                suffix = f" (exit code {code})" if code is not None else ""
                raise ProviderError(f"{model} did not load on {self._config.name}{suffix}.")
        else:
            while _status_of(self._entry(model)) in _RUNNING:
                time.sleep(_LOAD_POLL_SECONDS)

    def _entry(self, model: str) -> dict[str, Any] | None:
        data = http.get_json(
            f"{self._config.url}/models",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        raw = data.get("data") if isinstance(data, dict) else None
        for entry in raw or []:
            if isinstance(entry, dict) and entry.get("id") == model:
                return entry
        return None

    def _props(self, model: str, timeout: float) -> dict[str, Any] | None:
        # The name rides as a query, which is how a router forwards a GET.
        url = f"{self._config.base_url}/props?model={quote(model, safe='')}"
        return _get_props(self._config, self._auth, url, timeout)


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
        # count is of what the template renders for it.
        body = requests.chat_completion_body(model, messages, {})
        request = {k: v for k, v in body.items() if k not in ("stream", "stream_options")}
        data = http.post_json(
            f"{self._config.url}/chat/completions/input_tokens",
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
            f"{self._config.base_url}/tokenize",
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


class LlamaCppClient(OpenAIClient):
    id = "llamacpp"
    label = "llama.cpp"
    locality = Locality.LOCAL
    env_key = "LLAMACPP_API_KEY"
    models_class = LlamaCppModels
    completion_class = LlamaCppCompletion

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # Configured by launch flags, nothing on disk: a running server's
        # own flag is read, else the standard default.
        port = launched_port("llama-server") or 8080
        return ProviderConfig(name=cls.id, url=f"http://localhost:{port}/v1")


def _get_props(
    config: ProviderConfig, auth: OpenAIAuth, url: str, timeout: float
) -> dict[str, Any] | None:
    data = http.get_json(url, name=config.name, headers=auth.headers, timeout=timeout, quiet=True)
    return data if isinstance(data, dict) else None


def _with_props(model: ModelInfo, props: dict[str, Any]) -> ModelInfo:
    """`model` as a server's props describe it: loaded, with its window
    and its capabilities, checked."""
    return replace(
        model,
        max_context_loaded=_window_of(props),
        capabilities=_capabilities_of(props),
        state=ModelState.LOADED,
        checked=True,
    )


def _capabilities_of(props: dict[str, Any]) -> Capabilities:
    """What a server's props say its model can do: the modalities. The
    raw wire is always there and decoding is constrained server-side;
    which efforts the template grades, the server cannot say."""
    modalities = props.get("modalities")
    if not isinstance(modalities, dict):
        modalities = {}
    return Capabilities(
        vision=bool(modalities.get("vision")) if "vision" in modalities else None,
        audio=bool(modalities.get("audio")) if "audio" in modalities else None,
        text_completion=True,
        structured_output=True,
    )


def _window_of(props: dict[str, Any] | None) -> int | None:
    """The loaded context size a server's props report — per slot, which
    is what one request gets."""
    settings = props.get("default_generation_settings") if props is not None else None
    return positive_int(settings.get("n_ctx")) if isinstance(settings, dict) else None


def _state_of(entry: dict[str, Any] | None) -> ModelState:
    """A router entry's status as a state; no entry is unloaded, since
    the router lists every model it fronts."""
    status = _status_of(entry)
    if status in _RUNNING:
        return ModelState.LOADED
    if status == "loading":
        return ModelState.LOADING
    return ModelState.UNLOADED


def _status_of(entry: dict[str, Any] | None) -> str | None:
    """A router entry's status word — a plain string, or an object whose
    `value` it is, depending on the build; None for no entry."""
    status = entry.get("status") if entry else None
    if isinstance(status, dict):
        status = status.get("value")
    return status if isinstance(status, str) else None


def _exit_code_of(entry: dict[str, Any] | None) -> int | None:
    """The exit code a router keeps on a failed entry's status object."""
    status = entry.get("status") if entry else None
    code = status.get("exit_code") if isinstance(status, dict) else None
    return code if isinstance(code, int) else None
