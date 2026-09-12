"""The model half of a client: what a provider offers, and what each
model can do.

Two kinds of fact, two rules. The MODEL's own — capabilities, the
catalogue context size, the size on disk — are read once and cached
for the session; None means not read yet. The INSTANCE's — the model's
state, and the context size it was loaded with — belong to the engine,
which loads and unloads on its own, so they are asked live on every
`get`; only when the engine does not answer does the last word stand.
The listing is asked of the engine every time.

Hooks read the engine and nothing else; the public methods read and
write the cache and call the hooks. Engines override `_list`, `_decode`,
`_enhance`, `_state` and, where they manage models, `load` and
`unload`; a catalog sets `listing_keyed` and `listing_query` instead of
a listing of its own. The half reads the server off its config and the
key off its auth, which it asks to verify the key before a keyed
listing.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, replace
from typing import Any, ClassVar, final

from otaku.providers import http
from otaku.providers.errors import ProviderError, UnauthorizedError
from otaku.providers.http import ASK_TIMEOUT
from otaku.providers.openai.auth import OpenAIAuth
from otaku.settings.providers import ProviderConfig


@dataclass(frozen=True)
class Capabilities:
    """What a model can do, as its engine reports it. None: the engine
    cannot say, which a reader treats as no. `reasoning` is the set of
    efforts honoured, "none" among them when reasoning can be switched
    off; empty means no effort reaches the model."""

    vision: bool | None = None  # takes images on a message
    audio: bool | None = None  # takes audio on a message
    reasoning: frozenset[str] | None = None
    text_completion: bool | None = None  # the raw text wire exists for it
    structured_output: bool | None = None  # can be held to a JSON schema, or json mode


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
    max_context_catalogue: int | None = None  # the model's own ceiling
    max_context_loaded: int | None = None  # what the loaded instance serves
    # The most a reply may run, where a catalog states it (OpenRouter's
    # top_provider.max_completion_tokens); the local engines set none.
    max_output_tokens: int | None = None
    capabilities: Capabilities | None = None
    state: ModelState = ModelState.UNKNOWN
    checked: bool = False  # the per-model call (`_enhance`) has answered

    @property
    def max_context(self) -> int | None:
        """What a request gets: the loaded instance's context size, or the
        model's own where loading is not a thing (a catalog). None for
        a model not loaded, or not known."""
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

    def __init__(self, config: ProviderConfig, auth: OpenAIAuth) -> None:
        self._config = config
        self._auth = auth
        # The last listing, by name, with the model's own facts kept
        # across listings that could not read them. A listing is never
        # served from here — `list` reads it only to carry a model's own
        # facts into the next one; `get` serves the own facts from it
        # and lays the live state over them. The lock covers each read
        # and write, never a call to the engine: the picker's fan-out
        # and the session's turn share one client.
        self._cached_models: dict[str, ModelInfo] = {}
        self._lock = threading.Lock()

    # ---------- the protocol ----------

    @final
    def list(self, timeout: float = ASK_TIMEOUT) -> Listing:
        """The listing, asked of the engine every time. Raises the error
        family when the engine cannot answer, and forgets nothing then."""
        listed = self._list(timeout)
        fresh: dict[str, ModelInfo] = {}
        with self._lock:
            for model in listed:
                # The own facts a cached entry had read and this listing
                # lacks carry over, and `checked` with them.
                cached = self._cached_models.get(model.name)
                if cached is not None:
                    if model.capabilities is None:
                        model = replace(model, capabilities=cached.capabilities)
                    if model.max_context_catalogue is None:
                        model = replace(model, max_context_catalogue=cached.max_context_catalogue)
                    model = replace(model, checked=model.checked or cached.checked)
                fresh[model.name] = model
            self._cached_models = fresh
        return list(fresh.values())

    @final
    def get(self, name: str, timeout: float = ASK_TIMEOUT) -> ModelInfo | None:
        """One model: the cached entry, listed first when there is none;
        `_enhance` while unchecked; its state asked live. None when the
        provider does not offer it or cannot be reached."""
        model = self._cached_model(name)
        if model is None:
            try:
                self.list(timeout)
            except ProviderError:
                return None
            model = self._cached_model(name)
            if model is None:
                return None
        if not model.checked:
            model = self._enhance(model, timeout)
        live = self._state(model.name)
        if live is not None:
            state, max_context_loaded = live
            model = replace(model, state=state, max_context_loaded=max_context_loaded)
        with self._lock:
            self._cached_models[model.name] = model
        return model

    def load(self, model: str) -> None:
        """Blocks until the engine answers. Raises the error family; the
        base refuses, for an engine that does not manage models."""
        raise ProviderError(f"Models cannot be loaded or unloaded on {self._config.name}.")

    def unload(self, model: str) -> None:
        raise ProviderError(f"Models cannot be loaded or unloaded on {self._config.name}.")

    @property
    def can_manage(self) -> bool:
        """Whether `load` and `unload` work here."""
        return False

    # ---------- the hooks: each reads the engine and nothing else ----------

    def _list(self, timeout: float) -> Listing:
        """The engine's listing. The base reads the OpenAI /models:
        `context_length` as the catalogue size, `_decode` for what else
        an entry carries."""
        config = self._config
        if self.listing_keyed:
            if not self._auth.api_key:
                raise UnauthorizedError(f"No api key for {config.name}.")
            self._auth.verify_key(timeout)
        data = http.get_json(
            f"{config.url}/models{self.listing_query}",
            name=config.name,
            headers=self._auth.headers,
            timeout=timeout,
        )
        raw = data.get("data") if isinstance(data, dict) else None
        models = []
        for listed in raw if isinstance(raw, list) else []:
            if not isinstance(listed, dict) or not isinstance(listed.get("id"), str):
                continue
            model = ModelInfo(
                str(listed["id"]),
                max_context_catalogue=http.positive_int(listed.get("context_length")),
            )
            models.append(self._decode(listed, model))
        return sorted(models, key=lambda model: model.name)

    def _cached_model(self, name: str) -> ModelInfo | None:
        with self._lock:
            found = self._cached_models.get(name)
            return found if found is not None else self._cached_models.get(self._canonical(name))

    def _decode(self, listed: dict[str, Any], model: ModelInfo) -> ModelInfo:
        """`model` with what its /models entry states beyond the protocol
        (`_list`): a catalog's capabilities, served size, output limit.
        The base reads nothing more."""
        return model

    def _enhance(self, model: ModelInfo, timeout: float) -> ModelInfo:
        """`model` with what a per-model call adds beyond the listing
        (Ollama's card), `checked`; unchanged, still unchecked, when the
        surface did not answer, to be asked again. The base has nothing
        to add."""
        return replace(model, checked=True)

    def _canonical(self, name: str) -> str:
        """The listed name a shorthand stands for, which `get` falls back
        to when `name` itself is not listed: Ollama reads a bare name
        as its ":latest" tag. The base knows no shorthand."""
        return name

    def _state(self, name: str) -> tuple[ModelState, int | None] | None:
        """The instance's facts now: (the state, the loaded context size).
        None when the engine has no live surface, or it did not answer
        this time; the last word stands then. UNKNOWN is a state, not a
        missed answer."""
        return None
