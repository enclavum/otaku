"""The model half of a client: what a provider offers, and what each
model can do.

Two kinds of fact, two rules. The MODEL's own — capabilities, the
catalogue context size, the size on disk — are asked for once and
cached for the session; None means not read yet, and a later read
that states one fills it in. The INSTANCE's — the model's state, and
the context size it was loaded with — belong to the engine, which
loads and unloads on its own, so they are asked live on every `get`;
what the engine does not answer keeps its last word. The listing is
asked of the engine every time. `_merge` is the one rule of both,
serving the listing and the one-model read alike.

Hooks read the engine and nothing else, through the view they are
handed — one budget and one purpose for the whole ask; the public
methods make the view, read and write the cache and call the hooks.
Engines override `_list`, `_get`, `_decode`, `_canonical` and, where
they manage models, `load` and `unload`; a catalog sets `listing_keyed`
and `listing_query` instead of a listing of its own. The half reads the
server off its config and the key off its auth, which it asks to verify
the key before a keyed listing.
"""

from __future__ import annotations

import enum
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, ClassVar, final

from otaku.providers.errors import ProviderError, UnauthorizedError
from otaku.providers.http import (
    ASK_TIMEOUT,
    LOAD_TIMEOUT,
    Cut,
    Http,
    StreamCut,
    positive_int,
    ticking,
)
from otaku.providers.openai.auth import OpenAIAuth
from otaku.settings.providers import ProviderConfig


@dataclass(frozen=True)
class ModelCapabilities:
    """What a model can do, as its engine reports it. None: the engine
    cannot say, which a reader treats as no. Thinking is three facts,
    of which a model takes one shape of word (`reasoning`'s vocabulary):
    `reasoning_efforts` are the ladder's rungs it grades, "none" among
    them where that rung switches it off — empty, no rung reaches it;
    `reasoning_switch` is whether it is on or off and nothing between;
    `reasoning_budget` whether a budget in tokens holds, beside
    either."""

    vision: bool | None = None  # takes images on a message
    audio: bool | None = None  # takes audio on a message
    # The app's parameters this model honours, where a catalog states
    # them per route (OpenRouter); None where the provider's set is the
    # only word. Read through `OpenAIClient.supported_params_of`.
    supported_params: frozenset[str] | None = None
    reasoning_efforts: frozenset[str] | None = None
    reasoning_switch: bool | None = None
    reasoning_budget: bool | None = None
    text_completion: bool | None = None  # the raw text wire exists for it
    structured_output: bool | None = None  # can be held to a JSON schema, or json mode


class Locality(enum.Enum):
    """Where a server runs, as far as a client can tell: an engine
    knows, the generic provider is a url and cannot. A provider's, on
    its client; a model's own where a listing says it differs from its
    provider's (`ModelInfo.locality`) — Ollama serves a model marked
    `remote_host` from ollama.com through the local client — read for
    the model in use through `OpenAIClient.locality_of`. Every reader
    picks its safe side for UNKNOWN — what costs money or waits on the
    internet treats it as REMOTE, what edits the url treats it as
    LOCAL, and a caption says neither."""

    LOCAL = "local"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class ModelState(enum.Enum):
    """Whether the model is in memory. UNKNOWN is an engine that cannot
    say: the generic provider, a catalog, where loading is not a thing,
    KoboldCpp behind a password. A request may be sent in any state —
    the engines that load on demand load on it."""

    LOADED = "loaded"  # a llama.cpp router's "sleeping" too: the next request wakes it
    UNLOADED = "unloaded"
    LOADING = "loading"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelInfo:
    """One model as its provider reports it. None on a field: the engine
    cannot say."""

    name: str
    size: int | None = None  # bytes on disk; local engines only
    max_context_catalogue: int | None = None  # the model's own max context
    # What the loaded instance serves — or, on an engine that loads on
    # demand under a cap it states beforehand (omlx), what a request gets.
    max_context_loaded: int | None = None
    # The most a reply may run, where a catalog states it (OpenRouter's
    # top_provider.max_completion_tokens); the local engines set none.
    max_output_tokens: int | None = None
    capabilities: ModelCapabilities | None = None
    state: ModelState = ModelState.UNKNOWN
    # Where the model is served, when that is not where its provider's
    # server runs: None, and it is the provider's.
    locality: Locality | None = None

    @property
    def max_context(self) -> int | None:
        """What a request gets: the loaded instance's context size (the
        cap a request would load under, where the engine states one), or
        the model's own where loading is not a thing (a catalog). None
        for a model not loaded, or not known."""
        if self.max_context_loaded is not None:
            return self.max_context_loaded
        return self.max_context_catalogue if self.state is ModelState.UNKNOWN else None


# Named at module level, where `list` is still the builtin: the class
# below has a method of that name.
Listing = list[ModelInfo]


class OpenAIModels:
    # ---------- class knowledge ----------

    # The listing needs a key the account accepts: a catalog may be
    # public, so a listing alone proves nothing.
    listing_keyed: ClassVar[bool] = False
    # What /models wants appended for the details (NanoGPT's).
    listing_query: ClassVar[str] = ""

    def __init__(self, config: ProviderConfig, auth: OpenAIAuth, http: Http) -> None:
        self._config = config
        self._auth = auth
        self._http = http
        # The last listing, by name, with the model's own facts kept
        # across listings that could not read them. A listing is never
        # served from here — `list` reads it only to carry a model's own
        # facts into the next one; `get` serves the cached entry with
        # the engine's word merged over it. The lock covers each read
        # and write, never a call to the engine: the picker's fan-out
        # and the session's turn share one client.
        self._cached_models: dict[str, ModelInfo] = {}
        self._lock = threading.Lock()

    # ---------- the protocol ----------

    @final
    def list(self, timeout: float = ASK_TIMEOUT) -> Listing:
        """The listing, asked of the engine every time. Raises the error
        family when the engine cannot answer, and forgets nothing then."""
        listed = self._list(self._http.within(timeout, "listing"))
        fresh: dict[str, ModelInfo] = {}
        with self._lock:
            for model in listed:
                cached = self._cached_models.get(model.name)
                fresh[model.name] = model if cached is None else self._merge(cached, model)
            self._cached_models = fresh
        return list(fresh.values())

    @final
    def get(self, name: str, timeout: float = ASK_TIMEOUT) -> ModelInfo | None:
        """One model as the engine reports it now: the cached entry,
        listed first when there is none, with the engine's word (`_get`)
        merged over it. None when the provider does not offer it or
        cannot be reached."""
        model = self.cached(name)
        if model is None:
            try:
                self.list(timeout)
            except ProviderError:
                return None
            model = self.cached(name)
            if model is None:
                return None
        fresh = self._get(model.name, self._http.within(timeout, "model"))
        if fresh is not None:
            model = self._merge(model, fresh)
        with self._lock:
            self._cached_models[model.name] = model
        return model

    @final
    def ready(
        self,
        name: str,
        timeout: float = ASK_TIMEOUT,
        on_idle: Callable[[], bool | None] | None = None,
    ) -> ModelInfo | None:
        """`get`, the model loaded first where the engine manages loads
        and is not running it: a request would load it anyway, and the
        instance's facts — above all the window a prompt must fit — are
        known only once it runs. What a turn and the warm-up read, so
        neither budgets a cold model blind: Ollama sizes a runner as it
        loads and drops a prompt's head to fit it without a word. Waits
        as `load` waits, ticking `on_idle` meanwhile, and raises the
        error family as it does; None as `get` answers None."""
        found = self.get(name, timeout)
        if found is None or not self.can_manage:
            return found
        if found.state not in (ModelState.UNLOADED, ModelState.LOADING):
            return found
        self.load(found.name, on_idle=on_idle)
        return self.get(found.name, timeout) or found

    @final
    def cached(self, name: str) -> ModelInfo | None:
        """The model as last listed or read, nothing asked of the engine —
        for a reader that must not wait on the wire, the launch's banner
        on a provider whose url could name a catalog. Under `name` or the
        listed name it stands for (`_canonical`). None before anything
        listed it."""
        with self._lock:
            found = self._cached_models.get(name)
            return found if found is not None else self._cached_models.get(self._canonical(name))

    @final
    def load(
        self,
        model: str,
        *,
        timeout: float = LOAD_TIMEOUT,
        on_idle: Callable[[], bool | None] | None = None,
    ) -> None:
        """Load `model` and wait until the engine says it runs, under
        `timeout` for the whole sequence — weights read from disk, or
        fetched on demand — spent as unreachable. `on_idle`, given, is
        ticked on the caller's thread while the wait lasts, and may
        answer False to say nobody waits any more: the sequence is cut
        then, and raises as cut short. Raises the error family; refuses
        on an engine that does not manage models."""
        self._order("load", model, timeout, on_idle, self._load)

    @final
    def unload(
        self,
        model: str,
        *,
        timeout: float = LOAD_TIMEOUT,
        on_idle: Callable[[], bool | None] | None = None,
    ) -> None:
        """Unload `model` and wait until the engine says it is gone; as
        `load` in every other respect."""
        self._order("unload", model, timeout, on_idle, self._unload)

    def _order(
        self,
        purpose: str,
        model: str,
        timeout: float,
        on_idle: Callable[[], bool | None] | None,
        hook: Callable[[str, Http, Cut], None],
    ) -> None:
        """One order to the engine, `hook`, under one budget and one cut:
        run on the caller's thread, or on one of its own while the
        caller ticks `on_idle`."""
        if not self.can_manage:
            raise ProviderError(f"Models cannot be loaded or unloaded on {self._config.name}.")
        http = self._http.within(timeout, purpose)
        cut = Cut()
        try:
            if on_idle is None:
                hook(model, http, cut)
            else:
                ticking(lambda: hook(model, http, cut), on_idle, cut)
        except StreamCut:
            raise ProviderError(f"The {purpose} of {model} was cut short.") from None

    @property
    def can_manage(self) -> bool:
        """Whether `load` and `unload` work here."""
        return False

    # ---------- the cache: one rule ----------

    @final
    def _merge(self, cached: ModelInfo, fresh: ModelInfo) -> ModelInfo:
        """`cached` brought up to date with `fresh`, the one rule of what
        an entry means: the instance's facts are the engine's word now,
        and so is each of the model's own that `fresh` states — the
        cache fills in the ones it did not state this time. None is the
        one "not stated" value: no fact here is ever 0."""
        return replace(
            fresh,
            size=fresh.size or cached.size,
            max_context_catalogue=fresh.max_context_catalogue or cached.max_context_catalogue,
            max_output_tokens=fresh.max_output_tokens or cached.max_output_tokens,
            capabilities=fresh.capabilities or cached.capabilities,
            locality=fresh.locality or cached.locality,
        )

    # ---------- the hooks: each reads the engine and nothing else ----------

    def _list(self, http: Http) -> Listing:
        """The engine's listing, asked through the listing's view. The
        base reads the OpenAI /models: the model's own size as a
        catalog's `context_length` or llama.cpp's trained length in
        `meta`, the served window as the vLLM extension `max_model_len`
        (omlx and SGLang emit it too) or llama.cpp's slot in `meta` —
        what a request gets, from a server the generic provider cannot
        otherwise ask; `_decode` for what else an entry carries."""
        config = self._config
        if self.listing_keyed:
            if not self._auth.api_key:
                raise UnauthorizedError(f"No api key for {config.name}.")
            self._auth.verify_key(http)
        data = http.get(f"{config.url}/models{self.listing_query}")
        raw = data.get("data") if isinstance(data, dict) else None
        models = []
        for listed in raw if isinstance(raw, list) else []:
            if not isinstance(listed, dict) or not isinstance(listed.get("id"), str):
                continue
            meta = listed.get("meta")
            meta = meta if isinstance(meta, dict) else {}
            model = ModelInfo(
                str(listed["id"]),
                max_context_catalogue=positive_int(listed.get("context_length"))
                or positive_int(meta.get("n_ctx_train")),
                max_context_loaded=positive_int(listed.get("max_model_len"))
                or positive_int(meta.get("n_ctx")),
            )
            models.append(self._decode(listed, model))
        return sorted(models, key=lambda model: model.name)

    def _load(self, model: str, http: Http, cut: Cut) -> None:
        """The engine's load order and the wait for it, through the
        view `http` — one budget for the sequence — every request of
        it handed `cut`, and every poll of it ending when the cut is
        asked. An engine that manages models overrides; the base has
        nothing to order."""
        raise ProviderError(f"Models cannot be loaded or unloaded on {self._config.name}.")

    def _unload(self, model: str, http: Http, cut: Cut) -> None:
        raise ProviderError(f"Models cannot be loaded or unloaded on {self._config.name}.")

    def _get(self, name: str, http: Http) -> ModelInfo | None:
        """What the engine says of `name` now, asked through the model's
        view and built from that alone: the instance's facts — the
        state, the loaded context size — and whatever of the model's own
        the same surfaces state (the router's meta while the model runs,
        Ollama's card). None when the engine has no live surface, as a
        catalog has none, or did not answer this time: the cached word
        stands then. UNKNOWN is a state, not a missed answer. The base
        has no live surface."""
        return None

    def _decode(self, listed: dict[str, Any], model: ModelInfo) -> ModelInfo:
        """`model` with what its /models entry states beyond the protocol
        (`_list`): a catalog's capabilities, served size, output limit.
        The base reads nothing more."""
        return model

    def _canonical(self, name: str) -> str:
        """The listed name a shorthand stands for, which `get` falls back
        to when `name` itself is not listed: Ollama reads a bare name
        as its ":latest" tag. The base knows no shorthand."""
        return name
