"""The client: who the engine is and what it owns. The identity is
class knowledge (its id, the panel's caption, where the server runs,
the key's environment variable) plus the configuration. It owns the
`auth` (the key in force), the one transport built on it, and the two
halves, `models` and `completion`, each built from the class the engine
names; the halves are unrelated, read only what they are handed, and
the client is the one that knows both — and composes, from their
facts, `ProviderCapabilities`: what the provider can do, as one object
for the rest of the app. A local engine's url names its
server, and the surfaces hang off it: the native ones at the root, the
OpenAI one at /v1 — whatever was typed. The account's balance, which
only a catalog has, is the client's own.
"""

from dataclasses import dataclass, replace
from typing import ClassVar

from otaku.formatting import Money
from otaku.providers.http import ASK_TIMEOUT, ErrorSink, Http
from otaku.providers.openai.auth import KeySource, OpenAIAuth
from otaku.providers.openai.completion import Bounds, OpenAICompletion, RequestSink
from otaku.providers.openai.models import Locality, OpenAIModels
from otaku.settings.providers import ProviderConfig


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can do, as one object for the rest of the app —
    the mirror of `ModelCapabilities`. Composed by the client from the
    halves' own facts, each kept where the wire it describes is read;
    nothing is decided here."""

    tokenizer: bool  # the engine lends its own: the counts answer
    prompt_cache: bool  # breakpoints are honoured
    model_management: bool  # `load` and `unload` work
    supported_params: frozenset[str]  # the app's parameters the wire reads
    bounds: dict[str, Bounds]  # the engine's own bounds, where they differ from the app's table


class OpenAIClient:
    # ---------- class knowledge: who the engine is ----------

    id: ClassVar[str]  # the engine's, wherever one is named: the registry, a section, a report
    label: ClassVar[str]  # how the provider panel captions it
    locality: ClassVar[Locality] = Locality.UNKNOWN
    # The environment variable a missing key is read from; "" reads none.
    env_key: ClassVar[str] = ""
    # The parts, as the engine implements them.
    auth_class: ClassVar[type[OpenAIAuth]] = OpenAIAuth
    models_class: ClassVar[type[OpenAIModels]] = OpenAIModels
    completion_class: ClassVar[type[OpenAICompletion]] = OpenAICompletion

    def __init__(
        self,
        config: ProviderConfig,
        *,
        request_sink: RequestSink | None = None,
        error_sink: ErrorSink | None = None,
        smooth: bool = False,
    ) -> None:
        if self.locality is Locality.LOCAL and config.url:
            # A url typed without /v1 lists — Ollama's and LM Studio's
            # roots answer — and then fails every turn with a 404.
            root = config.url.rstrip("/").removesuffix("/v1")
            config = replace(config, url=f"{root}/v1")
        self.config = config
        self.auth = self.auth_class(config, self.env_key)
        self._http = Http(config.name, self.auth.headers, error_sink)
        self.models = self.models_class(config, self.auth, self._http)
        self.completion = self.completion_class(
            config, self._http, request_sink=request_sink, smooth=smooth, models=self.models
        )

    def supported_params_of(self, model: str) -> frozenset[str]:
        """The app's parameters that reach `model`: the wire's set, and
        within it the route's own where a catalog states one — what a
        menu offers for the model in use, and what the wire sends."""
        return self.completion.honoured(model)

    def locality_of(self, model: str) -> Locality:
        """Where `model` is served, as far as the client can tell: the
        row's own where a listing stated one — Ollama's rows served by
        ollama.com — and the client's otherwise. Off the cache, never
        over the wire: a reader asking where a request would go must
        not send one to find out."""
        found = self.models.cached(model)
        if found is not None and found.locality is not None:
            return found.locality
        return self.locality

    @property
    def capabilities(self) -> ProviderCapabilities:
        """What this provider can do, composed on every read: one fact is
        live — a llama.cpp client cannot manage models until a listing
        has told it whether it fronts a router."""
        return ProviderCapabilities(
            tokenizer=self.completion.can_count_tokens,
            prompt_cache=self.completion.can_mark_cache,
            model_management=self.models.can_manage,
            supported_params=self.completion.supported_params,
            bounds=self.completion.bounds,
        )

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The engine's default provider section: what the panel shows
        before an engine is configured, and what first run writes for
        the local engines. No key: the environment variable's is read
        at request time, never written into a section."""
        return ProviderConfig(name=cls.id, url="")

    @classmethod
    def key_source(cls, config: ProviderConfig) -> KeySource | None:
        """Where the key `config` would be asked with comes from — its
        section's, the engine's environment variable, or none — saved or
        not, and no client built: what a panel captions the field with,
        the value never shown."""
        return cls.auth_class(config, cls.env_key).key_source

    def balance(self, timeout: float = ASK_TIMEOUT) -> Money | None:
        """The account balance as the provider reports it — None where
        there is no account or it will not say. Money, not a rendered
        string: what a reader sees is the frontends' to decide."""
        return None
