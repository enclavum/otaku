"""llama.cpp's server (`llama-server`), in either of its two modes: one
model chosen at launch, or the router (`--models-dir`), which fronts a
folder of models and loads and unloads them by name. Chat rides the
OpenAI protocol at /v1. The listing carries what a model half needs
— the loaded context size, the model's own max context and its size
in `meta`, and in router mode the modalities of every entry — so
`/props` is read once per listing: the modalities in single mode, and
the key check in both, `/v1/models` being exempt from the server's key
check where `/props` is not, so a wrong key, or none where one is
demanded, shows at the listing rather than at the first turn. How a
model's thinking is set, the server states nowhere: its template is
asked, through `/apply-template`, once per model. Nothing is read for
a single server's state, which never changes. A router's entries carry
a `status`, which is how the mode is told: `can_manage` is settled by
the first listing and false until then. In router mode any request for
an unloaded model loads it, so the counts and the probe ask not to.
"""

import re
import time
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from otaku.providers.clients import launched_port
from otaku.providers.errors import ProviderError, StatusError, UnreachableError
from otaku.providers.http import ASK_TIMEOUT, PROBE_TIMEOUT, Http, positive_int
from otaku.providers.openai import reasoning
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import PROTOCOL_PARAMS, SAMPLER_PARAMS, OpenAICompletion
from otaku.providers.openai.models import (
    Listing,
    ModelCapabilities,
    ModelInfo,
    ModelState,
    OpenAIModels,
)
from otaku.providers.openai.requests import Image, WireMessage
from otaku.settings.providers import ProviderConfig

# How a model's thinking is set, as its template told it: (the rungs
# graded, whether it switches, whether a budget holds) — all None while
# the template has not been asked, or answered nothing.
_Thinking = tuple[frozenset[str] | None, bool | None, bool | None]
_UNKNOWN_THINKING: _Thinking = (None, None, None)
# The one turn the template is rendered over, three ways.
_PROBE_MESSAGES = [{"role": "user", "content": "Hello"}]

_LOAD_POLL_SECONDS = 0.5  # how often a router is asked whether an order is done
_SILENCE_BUDGET = 10.0  # how long a router may leave polls unanswered before the order is lost
# A router entry's words for a model with a server behind it: loaded, or
# put to sleep idle (`--sleep-idle-seconds`) and woken by the next request.
_RUNNING = frozenset({"loaded", "sleeping"})
# Its words for a load under way — fetched first, where a build past
# b9290 downloads on demand — that an order waits on, not repeats.
_PENDING = frozenset({"downloading", "downloaded", "loading"})


class LlamaCppModels(OpenAIModels):
    """The reads both modes share: the listing, which also tells the
    mode — a router's entries carry a `status` — and `/props`. Every
    listing swaps the instance to the mode's own class, so a server
    restarted the other way is followed; the cache and the lock stay.
    A fresh client is a single server until a listing says otherwise."""

    def __init__(self, config: ProviderConfig, auth: OpenAIAuth, http: Http) -> None:
        super().__init__(config, auth, http)
        self._templates: dict[str, _Thinking] = {}  # what each model's template told, by name

    def _list(self, http: Http) -> Listing:
        data = http.get(f"{self._config.url}/models")
        # Loud: a 401 is the key's verdict, wrong or missing. A 404 alone
        # is tolerated — a build without /props is still llama.cpp, its
        # modalities unknown.
        try:
            props = http.get(f"{self._config.base_url}/props")
        except StatusError as e:
            if e.status != 404:
                raise
            props = None
        entries = self._entries_of(data)
        # Told by the raw entries, projectors included: a folder of
        # nothing but a projector is still a router.
        is_router = any("status" in entry for entry in entries)
        self.__class__ = LlamaCppRouterModels if is_router else LlamaCppSingleModels
        return self._listing([e for e in entries if not self._is_projector(e)], props, http)

    def _listing(self, entries: list[dict[str, Any]], props: Any, http: Http) -> Listing:
        """The entries as the mode reads them — each mode's own."""
        raise NotImplementedError

    def _entries_of(self, data: object) -> list[dict[str, Any]]:
        """The listing's entries with a string id."""
        raw = data.get("data") if isinstance(data, dict) else None
        return [e for e in raw or [] if isinstance(e, dict) and isinstance(e.get("id"), str)]

    def _is_projector(self, entry: dict[str, Any]) -> bool:
        """Whether an entry is a projector, not a model: a router lists a
        top-level `mmproj-*.gguf` as one (llama.cpp pairs a projector
        with its model only inside a subfolder) and loading it fails
        with an exit code. The name is how llama.cpp tells one, read
        here in any case and in either mode: a projector is no model
        wherever it is listed."""
        return "mmproj" in str(entry["id"]).lower()

    def _meta_of(self, entry: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
        """(the loaded context size, the model's own max context, the
        size on disk) from an entry's `meta`: `n_ctx` is per slot, what
        one request gets."""
        meta = entry.get("meta")
        if not isinstance(meta, dict):
            return None, None, None
        return (
            positive_int(meta.get("n_ctx")),
            positive_int(meta.get("n_ctx_train")),
            positive_int(meta.get("size")),
        )

    def _capabilities_of(self, modalities: object, thinking: _Thinking) -> ModelCapabilities:
        """What the modalities say — a router entry's `input_modalities`
        list, or a props object's `modalities` flags; neither leaves
        vision and audio unknown — and what the template told
        (`_thinking_of`). The raw wire is always there and decoding is
        constrained server-side."""
        vision: bool | None
        audio: bool | None
        if isinstance(modalities, list):
            vision, audio = "image" in modalities, "audio" in modalities
        elif isinstance(modalities, dict):
            vision = bool(modalities.get("vision")) if "vision" in modalities else None
            audio = bool(modalities.get("audio")) if "audio" in modalities else None
        else:
            vision = audio = None
        levels, switch, budget = thinking
        return ModelCapabilities(
            vision=vision,
            audio=audio,
            reasoning_efforts=levels,
            reasoning_switch=switch,
            reasoning_budget=budget,
            text_completion=True,
            structured_output=True,
        )

    def _thinking_of(self, model: str, http: Http) -> _Thinking:
        """How `model`'s thinking is set, as its template tells it,
        asked once: the prompt rendered by `/apply-template` with the
        flag on and off, and with two efforts — what changes the prompt
        is what the template reads. An effort it reads grades the
        thinking: every rung, "none" being the budget's off. A flag
        alone switches it. The budget holds on either: the server's own
        sampler. A template that reads neither leaves all three
        unknown — the server cannot say a model does not think, and a
        thinking one the budget still stops — as does a render that
        did not come: an old build without the endpoint, a router whose
        model is not running (the render needs it loaded, and a probe
        must not load it)."""
        known = self._templates.get(model)
        if known is not None:
            return known
        on_low = self._render(model, {"enable_thinking": True, "reasoning_effort": "low"}, http)
        off_low = self._render(model, {"enable_thinking": False, "reasoning_effort": "low"}, http)
        on_high = self._render(model, {"enable_thinking": True, "reasoning_effort": "high"}, http)
        if on_low is None or off_low is None or on_high is None:
            return _UNKNOWN_THINKING
        thinking: _Thinking
        if on_low != on_high:
            thinking = (reasoning.ALL_EFFORT_LEVELS, False, True)
        elif on_low != off_low:
            thinking = (frozenset(), True, True)
        else:
            thinking = _UNKNOWN_THINKING
        self._templates[model] = thinking
        return thinking

    def _render(self, model: str, kwargs: dict[str, object], http: Http) -> str | None:
        """The prompt the template renders for one turn under `kwargs`;
        None where the server did not answer."""
        data = http.post(
            f"{self._config.base_url}/apply-template?autoload=false",
            {"model": model, "messages": _PROBE_MESSAGES, "chat_template_kwargs": kwargs},
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        prompt = data.get("prompt") if isinstance(data, dict) else None
        return prompt if isinstance(prompt, str) else None


class LlamaCppSingleModels(LlamaCppModels):
    """One model, loaded at launch, its state never changing: the
    listing's one entry carries its loaded context size, its own max
    context and size, `/props` its modalities, and every listed name
    is that model's — a listing that came back long, a catalog's url
    pasted into the section, still costs the one probe, stamped on
    every row. The name is the model file's: a build past b9290 lists
    the whole path, and the server serves its one model whatever a
    request names, so the file name every build agrees on is what a
    section remembers. Nothing is asked live, nothing is managed."""

    def _listing(self, entries: list[dict[str, Any]], props: Any, http: Http) -> Listing:
        if not entries:
            return []
        max_context_loaded, max_context_catalogue, size = self._meta_of(entries[0])
        # Props without `modalities` are a build older than mid-2025:
        # still llama.cpp, vision and audio unknown. No props, nothing
        # is known of the modalities; the template is asked all the same.
        answered = isinstance(props, dict)
        modalities = props.get("modalities") if answered else None
        # The file name, however the build lists it: the name alone
        # (b9290) or the whole path (past it), either separator.
        names = [re.split(r"[\\/]", str(entry["id"]))[-1] for entry in entries]
        thinking = self._thinking_of(names[0], http)
        return sorted(
            (
                ModelInfo(
                    name=name,
                    size=size,
                    max_context_catalogue=max_context_catalogue,
                    max_context_loaded=max_context_loaded,
                    capabilities=self._capabilities_of(modalities, thinking),
                    state=ModelState.LOADED,
                )
                for name in names
            ),
            key=lambda model: model.name,
        )


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
        entry = self._entry(model, self._http)
        if entry == {}:
            # The router lists every model it fronts. A name it does not
            # — a top-level projector's, a typo — would spawn a child
            # doomed to die, or draw a 404 spelled "File Not Found".
            unknown = ProviderError(f"{model} is not offered by {self._config.name}.")
            self._http.record(unknown, "load")
            raise unknown
        status = self._status_of(entry or {})
        if status in _RUNNING:
            return
        # A load under way — the router autoloads on any request for the
        # model — is one to wait for, not to order again. The order goes
        # out quietly: the router's own word for one the probe missed is
        # a 400, no failure, and the poll judges what became of it.
        if status not in _PENDING:
            self._http.post(f"{self._config.base_url}/models/load", {"model": model}, quiet=True)
        entry = self._wait(model, lambda status: status in _PENDING, "load")
        if self._status_of(entry) not in _RUNNING:
            status_object = entry.get("status")
            code = status_object.get("exit_code") if isinstance(status_object, dict) else None
            suffix = f" (exit code {code})" if isinstance(code, int) else ""
            failed = ProviderError(f"{model} did not load on {self._config.name}{suffix}.")
            self._http.record(failed, "load")
            raise failed

    def unload(self, model: str) -> None:
        status = self._status_of(self._entry(model, self._http) or {})
        if status not in _RUNNING and status not in _PENDING:
            return  # nothing running, or on its way, to stop
        # A 400 is the router saying the model stopped between the probe
        # and the order — evicted, reaped asleep — which is what was
        # asked for. Loud otherwise: an order that failed would leave the
        # poll waiting on a model that never stops.
        try:
            self._http.post(
                f"{self._config.base_url}/models/unload", {"model": model}, purpose="unload"
            )
        except StatusError as e:
            if e.status != 400:
                raise
        self._wait(model, lambda status: status in _RUNNING or status in _PENDING, "unload")

    # ---------- the hooks ----------

    def _listing(self, entries: list[dict[str, Any]], props: Any, http: Http) -> Listing:
        # Every entry states its modalities; the loaded context size, the
        # model's own max context and size are merged in for a running
        # one, and its template is asked.
        models = []
        for entry in entries:
            max_context_loaded, max_context_catalogue, size = self._meta_of(entry)
            state = self._state_of(entry)
            name = str(entry["id"])
            models.append(
                ModelInfo(
                    name=name,
                    size=size,
                    max_context_catalogue=max_context_catalogue,
                    max_context_loaded=max_context_loaded,
                    capabilities=self._capabilities_of(
                        self._modalities_of(entry),
                        self._thinking_of(name, http)
                        if state is ModelState.LOADED
                        else _UNKNOWN_THINKING,
                    ),
                    state=state,
                )
            )
        return sorted(models, key=lambda model: model.name)

    def _get(self, name: str, http: Http) -> ModelInfo | None:
        entry = self._entry(name, http)
        if entry is None:
            return None
        # The meta rides only while the model runs, and the template can
        # only be asked then: the first read after a load is where its
        # own size, max context and thinking become known.
        max_context_loaded, max_context_catalogue, size = self._meta_of(entry)
        state = self._state_of(entry)
        return ModelInfo(
            name=name,
            size=size,
            max_context_catalogue=max_context_catalogue,
            max_context_loaded=max_context_loaded,
            capabilities=self._capabilities_of(
                self._modalities_of(entry), self._thinking_of(name, http)
            )
            if state is ModelState.LOADED
            else None,
            state=state,
        )

    # ---------- the router's own ----------

    def _entry(self, model: str, http: Http) -> dict[str, Any] | None:
        """The model's entry in the listing now, as a probe through
        `http` — the model's view, or the transport itself while a load
        polls: {} when the router lists no such model, None when it did
        not answer — which the polling tells apart."""
        data = http.get(f"{self._config.url}/models", timeout=PROBE_TIMEOUT, quiet=True)
        if not isinstance(data, dict):
            return None
        entries = (e for e in self._entries_of(data) if not self._is_projector(e))
        return next((e for e in entries if e.get("id") == model), {})

    def _wait(
        self, model: str, pending: Callable[[str | None], bool], purpose: str
    ) -> dict[str, Any]:
        """The model's entry once its status is no longer `pending`. A
        poll the router does not answer — it holds its lock while a
        child spawns — is waited out, up to a budget of silence, then
        filed under `purpose`."""
        quiet_since: float | None = None  # when the router's silence began
        while True:
            entry = self._entry(model, self._http)
            if entry is None:
                now = time.monotonic()
                if quiet_since is None:
                    quiet_since = now
                elif now - quiet_since > _SILENCE_BUDGET:
                    lost = UnreachableError(f"{self._config.name} stopped answering.")
                    self._http.record(lost, purpose)
                    raise lost
            elif not pending(self._status_of(entry)):
                return entry
            else:
                quiet_since = None
            time.sleep(_LOAD_POLL_SECONDS)

    def _modalities_of(self, entry: dict[str, Any]) -> object:
        """An entry's `architecture.input_modalities`, as listed."""
        architecture = entry.get("architecture")
        return architecture.get("input_modalities") if isinstance(architecture, dict) else None

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
        if status in ("loading", "downloading"):
            return ModelState.LOADING
        return ModelState.UNLOADED


class LlamaCppCompletion(OpenAICompletion):
    supported_params = PROTOCOL_PARAMS | SAMPLER_PARAMS
    # The template's flag is what stops Gemma 4 and its kind; the effort
    # rides beside it as a template variable, for the templates that
    # read one; and the budget is the server's own sampler, which stops
    # a template that reads neither (gpt-oss's, Kimi's). A request-level
    # reasoning_effort is not read. The budget is a sampling parameter,
    # so it holds on the raw wire too, where no template stands.
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {
            reasoning.SWITCH_TEMPLATE_KNOB,
            reasoning.EFFORT_TEMPLATE_KNOB,
            reasoning.BUDGET_TOKENS_KNOB,
        }
    )
    text_reasoning_knobs: ClassVar[frozenset[str]] = frozenset({reasoning.BUDGET_TOKENS_KNOB})
    # The chat count needs a build past b9290 (June 2026); an older one
    # answers None and the caller keeps its estimate.
    can_count_tokens = True

    def count_chat_tokens(
        self,
        model: str,
        messages: Sequence[WireMessage],
        *,
        level: str | None = None,
        images: Sequence[Image] = (),
        timeout: float = ASK_TIMEOUT,
    ) -> int | None:
        # The same body a turn would send, knobs included and streaming
        # fields aside, so the count is of what the template renders for
        # it. A router must not load the model for a count.
        body, knobs = self._chat_request(model, messages, {}, level=level, images=images)
        request = {
            k: v for k, v in {**body, **knobs}.items() if k not in ("stream", "stream_options")
        }
        data = self._http.post(
            f"{self._config.url}/chat/completions/input_tokens?autoload=false",
            request,
            timeout=timeout,
            quiet=True,
        )
        return positive_int(data.get("input_tokens")) if isinstance(data, dict) else None

    def count_text_tokens(
        self, model: str, prompt: str, timeout: float = ASK_TIMEOUT
    ) -> int | None:
        data = self._http.post(
            f"{self._config.base_url}/tokenize?autoload=false",
            # The model rides in the body, which is how a router forwards
            # a POST; a single server ignores it. `add_special` adds the
            # BOS the text wire counts and /tokenize leaves out.
            {"model": model, "content": prompt, "add_special": True},
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
