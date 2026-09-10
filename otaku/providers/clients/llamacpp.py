"""llama.cpp's server (`llama-server`), in either of its two modes: ONE
model chosen at launch — the usual — or the ROUTER (`--models-dir`,
`--models-preset`), which fronts a folder of models and loads and
unloads them by name. Chat rides the OpenAI protocol at /v1. The
native surface adds `/props` — the loaded window, the modalities, and
what the chat template can do — `/tokenize`, the token count of a chat
request, and the router's `/models` family. Which mode a server is in
shows in its listing: a router's rows carry a `status`, a single
server's carry `meta`. So `manages_models` is settled by the first
listing, and false until then.
"""

import time
from collections.abc import Sequence
from typing import Any, ClassVar
from urllib.parse import quote

from otaku.providers import http, wire
from otaku.providers.client import (
    ASK_TIMEOUT,
    PROBE_TIMEOUT,
    Capabilities,
    Client,
    Locality,
    ModelInfo,
    RequestSink,
)
from otaku.providers.clients import launched_port
from otaku.providers.wire import (
    ALL_THINKING_LEVELS,
    THINKING_EFFORT_KNOB,
    THINKING_FLAG_KNOB,
    WireMessage,
    positive_int,
)
from otaku.settings.providers import ProviderConfig

_LOAD_POLL_SECONDS = 0.5  # how often a router is asked whether a load is done


class LlamaCppClient(Client):
    kind = "llamacpp"
    label = "llama.cpp"
    locality = Locality.LOCAL
    env_key = "LLAMACPP_API_KEY"
    # The template's flag is what stops Gemma 4 and its kind; the effort
    # is applied where the template takes one (`chat_template_caps`).
    thinking_knobs: ClassVar[frozenset[str]] = frozenset({THINKING_EFFORT_KNOB, THINKING_FLAG_KNOB})
    counts_tokens = True

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # Configured by launch flags — nothing on disk to detect a port
        # from, so a running server's own flag is read, else the standard
        # default.
        port = launched_port("llama-server") or 8080
        return ProviderConfig(name=cls.kind, url=f"http://localhost:{port}/v1")

    def __init__(
        self,
        config: ProviderConfig,
        *,
        request_sink: RequestSink | None = None,
        smooth: bool = False,
    ) -> None:
        super().__init__(config, request_sink=request_sink, smooth=smooth)
        self._is_router = False

    @property
    def manages_models(self) -> bool:
        return self._is_router

    def load_model(self, model: str) -> None:
        if not self._is_router:
            return super().load_model(model)
        self._router_action("load", model)

    def unload_model(self, model: str) -> None:
        if not self._is_router:
            return super().unload_model(model)
        self._router_action("unload", model)

    def _router_action(self, action: str, model: str) -> None:
        """One router order, then the wait: the router answers as soon as
        the order is taken, and the model's own server comes up — or
        goes down — after. A load is done when the row stops saying
        "loading", an unload when it stops saying "loaded"."""
        http.post_json(
            f"{self.config.base_url}/models/{action}",
            {"model": model},
            name=self.config.name,
            headers=self._headers,
            timeout=None,
        )
        pending = "loading" if action == "load" else "loaded"
        while self._router_status(model) == pending:
            time.sleep(_LOAD_POLL_SECONDS)

    def _router_status(self, model: str) -> str | None:
        data = http.get_json(
            f"{self.config.url}/models",
            name=self.config.name,
            headers=self._headers,
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        raw = data.get("data") if isinstance(data, dict) else None
        for entry in raw or []:
            if isinstance(entry, dict) and entry.get("id") == model:
                return _status_of(entry)
        return None

    def models(self, timeout: float = ASK_TIMEOUT) -> list[ModelInfo]:
        data = http.get_json(
            f"{self.config.url}/models",
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
        )
        raw = data.get("data") if isinstance(data, dict) else None
        entries = [e for e in raw or [] if isinstance(e, dict) and isinstance(e.get("id"), str)]
        self._is_router = any("status" in entry for entry in entries)
        if self._is_router:
            # A loaded model's own server answers for it; an unloaded one
            # has no server to ask.
            rows = []
            for entry in entries:
                name, loaded = str(entry["id"]), _status_of(entry) == "loaded"
                props = self._props(name, timeout) if loaded else None
                capabilities = self._props_capabilities(props)
                rows.append(ModelInfo(name=name, capabilities=capabilities, loaded=loaded))
            return sorted(rows, key=lambda row: row.name)
        # One model, loaded at launch: the context size and the
        # capabilities are the SERVER's, and one probe under the first
        # name stamps every row — a listing that came back long (a
        # catalog url pasted into the section) still costs one round
        # trip rather than one per name.
        names = sorted(str(entry["id"]) for entry in entries)
        props = self._props(names[0], timeout) if names else None
        size = _context_size_of(props)
        capabilities = self._props_capabilities(props)
        for name in names:
            if size:
                self._context_sizes[name] = size
        return [
            ModelInfo(name=name, capabilities=capabilities, context=size, loaded=True)
            for name in names
        ]

    def _props_capabilities(self, props: dict[str, Any] | None) -> Capabilities | None:
        """What a server's props say its model can do: the modalities,
        and whether the chat template applies an effort — with it every
        level counts, without it the flag alone does, which is "off" or
        the model's own default. No props (an unloaded model, a server
        that would not say) is nothing read."""
        if props is None:
            return None
        modalities = props.get("modalities")
        vision = bool(modalities.get("vision", True)) if isinstance(modalities, dict) else True
        caps = props.get("chat_template_caps")
        effort = isinstance(caps, dict) and bool(caps.get("supports_reasoning_effort"))
        thinking = ALL_THINKING_LEVELS if effort else frozenset({"off"})
        return Capabilities(vision=vision, thinking=thinking)

    def _context_size(self, model: str) -> int | None:
        return _context_size_of(self._props(model, timeout=PROBE_TIMEOUT))

    def count_chat_tokens(
        self, model: str, messages: Sequence[WireMessage], timeout: float = ASK_TIMEOUT
    ) -> int | None:
        # The same body a turn would send, streaming fields aside, so the
        # count is of what the template renders for it.
        body = wire.chat_completion_body(model, messages, {})
        request = {k: v for k, v in body.items() if k not in ("stream", "stream_options")}
        url = f"{self.config.url}/chat/completions/input_tokens"
        data = http.post_json(
            url, request, name=self.config.name, headers=self._headers, timeout=timeout, quiet=True
        )
        count = data.get("input_tokens") if isinstance(data, dict) else None
        return count if isinstance(count, int) else None

    def count_text_tokens(
        self, model: str, prompt: str, timeout: float = ASK_TIMEOUT
    ) -> int | None:
        # The model rides in the body, which is how a router forwards a
        # POST; a single server ignores it.
        url = f"{self.config.base_url}/tokenize"
        data = http.post_json(
            url,
            {"model": model, "content": prompt},
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
            quiet=True,
        )
        tokens = data.get("tokens") if isinstance(data, dict) else None
        return len(tokens) if isinstance(tokens, list) else None

    def _props(self, model: str, timeout: float) -> dict[str, Any] | None:
        """The server's props — in router mode, the named model's, the
        name riding as a query (how a router forwards a GET); a router
        asked without a name answers about itself."""
        url = f"{self.config.base_url}/props"
        if self._is_router:
            url += f"?model={quote(model, safe='')}"
        data = http.get_json(
            url, name=self.config.name, headers=self._headers, timeout=timeout, quiet=True
        )
        return data if isinstance(data, dict) else None


def _context_size_of(props: dict[str, Any] | None) -> int | None:
    """The loaded context size a server's props report — per slot,
    which is what one request gets."""
    settings = props.get("default_generation_settings") if props is not None else None
    return positive_int(settings.get("n_ctx")) if isinstance(settings, dict) else None


def _status_of(entry: dict[str, Any]) -> str | None:
    """A router row's status word — a plain string, or an object whose
    `value` it is, depending on the build."""
    status = entry.get("status")
    if isinstance(status, dict):
        status = status.get("value")
    return status if isinstance(status, str) else None
