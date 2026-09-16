"""Provider lookup and fan-out — and the probe that asks a provider
before it is saved.

The section name IS the engine: `ALL_CLIENTS` maps the eight names to
their clients in the model picker's canonical order — the generic
provider first, the engines on this machine, the catalogs — and a
section named anything else is not served: the registry leaves it out
and names it in `ignored`, for the launch to say so. One section per
engine; a second server of one kind is not a thing yet. First-run
autoconfiguration writes sections for the local engines only; the
generic provider and a cloud one are added deliberately, url and key
and all.

The `Registry` is composed by the backend package and injected
everywhere a client is resolved: the configured providers, the
request-log and error-log sinks, the smoothing flag, and the per-provider client
cache. File persistence is NOT here — the panel saves write through
settings and then call `update_provider`. Nothing here ever blocks or
exits the app: an unreachable provider is skipped in fan-outs, and a
dead one costs its own timeout — overlapped with the others, never
the launch.
"""

import enum
import os
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar

from otaku.providers.clients.generic import GenericClient
from otaku.providers.clients.koboldcpp import KoboldCppClient
from otaku.providers.clients.llamacpp import LlamaCppClient
from otaku.providers.clients.lmstudio import LmStudioClient
from otaku.providers.clients.nanogpt import NanoGptClient
from otaku.providers.clients.ollama import OllamaClient
from otaku.providers.clients.omlx import OmlxClient
from otaku.providers.clients.openrouter import OpenRouterClient
from otaku.providers.errors import ProviderError, UnauthorizedError, UnreachableError
from otaku.providers.http import LISTING_TIMEOUT, ErrorSink
from otaku.providers.openai.auth import KeySource
from otaku.providers.openai.client import Locality, OpenAIClient, ProviderCapabilities
from otaku.providers.openai.completion import RequestSink
from otaku.providers.openai.models import ModelInfo
from otaku.settings.providers import ProviderConfig

_T = TypeVar("_T")  # Registry.map's result type

ALL_CLIENTS: dict[str, type[OpenAIClient]] = {
    LlamaCppClient.id: LlamaCppClient,
    KoboldCppClient.id: KoboldCppClient,
    OllamaClient.id: OllamaClient,
    OmlxClient.id: OmlxClient,
    LmStudioClient.id: LmStudioClient,
    GenericClient.id: GenericClient,
    OpenRouterClient.id: OpenRouterClient,
    NanoGptClient.id: NanoGptClient,
}


@dataclass(frozen=True)
class ProviderInfo:
    """One reachable provider with its models — what `Registry.info`
    answers and the model pickers list, as data: the client's identity
    and what it can do, never the client."""

    id: str
    label: str
    locality: Locality
    key_source: KeySource | None
    capabilities: ProviderCapabilities
    models: list[ModelInfo]


class ProbeStatus(enum.Enum):
    """How a probe ended. A frontend reads only this; the sentence
    beside it is what it shows."""

    OK = "ok"
    EMPTY = "empty"  # the server answered, but lists no models
    UNREACHABLE = "unreachable"
    UNAUTHORIZED = "unauthorized"
    ERROR = "error"  # the server answered with an error status


@dataclass(frozen=True)
class Probe:
    """What `probe` found: the outcome, how many models the provider
    listed, where the key it was asked with came from, and the sentence
    that says it."""

    status: ProbeStatus
    models_count: int
    key_source: KeySource | None
    message: str


# Named at module level, where `list` is still the builtin: the class
# below has a method of that name.
Ids = list[str]
Results = list[_T]


class Registry:
    def __init__(
        self,
        configs: dict[str, ProviderConfig],
        *,
        request_sink: RequestSink | None = None,
        error_sink: ErrorSink | None = None,
        smooth: bool = False,
    ) -> None:
        # The sections an engine serves, and the names of those none does.
        self.configs = {name: config for name, config in configs.items() if name in ALL_CLIENTS}
        self.ignored: tuple[str, ...] = tuple(sorted(set(configs) - set(ALL_CLIENTS)))
        self._request_sink = request_sink
        self._error_sink = error_sink
        self._smooth = smooth
        self._clients: dict[str, OpenAIClient] = {}
        self._lock = threading.Lock()  # a fan-out builds beside a panel save

    def list(self) -> Ids:
        """The configured providers' ids, sorted."""
        return sorted(self.configs)

    def get(self, provider: str) -> OpenAIClient | None:
        """The named provider's client, cached — its engine chosen by the
        name (see the module docstring); None for a provider that is not
        configured."""
        with self._lock:
            if provider in self._clients:
                return self._clients[provider]
            config = self.configs.get(provider)
            if config is None:
                return None
            return self._build(config)

    def update(self, config: ProviderConfig) -> None:
        """Swap one provider's configuration for the running session and
        rebuild its client against the new url and key — the panel lists
        the models right after. Persisting the change is the caller's
        business. Raises ValueError for a name no engine answers to: the
        registry serves the engines' sections and founds no other."""
        if config.name not in ALL_CLIENTS:
            raise ValueError(f"no supported provider is named {config.name!r}")
        with self._lock:
            self.configs[config.name] = config
            self._build(config)

    def info(self, provider: str) -> ProviderInfo | None:
        """One provider's identity and models, listed now — None when it
        is not configured or cannot answer: a dead server, a rejected
        key. The pickers fan it out with `map`."""
        client = self.get(provider)
        if client is None:
            return None
        try:
            models = client.models.list(timeout=LISTING_TIMEOUT)
        except ProviderError:
            return None
        return ProviderInfo(
            client.id,
            client.label,
            client.locality,
            client.auth.key_source,
            client.capabilities,
            models,
        )

    def map(self, fn: Callable[[str], _T], ids: Iterable[str] | None = None) -> Results[_T]:
        """Run `fn(id)` for every configured provider — or the `ids`
        given — concurrently, results in that order: one dead provider's
        timeout overlaps the others instead of adding to them. `fn`
        handles its own errors; an exception propagates."""
        asked = list(self.configs if ids is None else ids)
        if not asked:
            return []
        with ThreadPoolExecutor(max_workers=len(asked)) as pool:
            return list(pool.map(fn, asked))

    def _build(self, config: ProviderConfig) -> OpenAIClient:
        client = ALL_CLIENTS[config.name](
            config,
            request_sink=self._request_sink,
            error_sink=self._error_sink,
            smooth=self._smooth,
        )
        self._clients[config.name] = client
        return client


def probe(config: ProviderConfig, *, timeout: float = LISTING_TIMEOUT) -> Probe:
    """Ask the provider `config` describes — saved or not — whether it
    answers and what it lists, the way the panel wants to know before
    Save. Never raises: unreachable, a rejected key, an error status and
    an empty listing are ANSWERS, each with the sentence that says it."""
    cls = ALL_CLIENTS.get(config.name)
    if cls is None:
        return Probe(ProbeStatus.ERROR, 0, None, f"No supported provider is named {config.name}.")
    client = cls(config)
    source = client.auth.key_source
    try:
        models = client.models.list(timeout)
    except UnreachableError as e:
        return Probe(ProbeStatus.UNREACHABLE, 0, source, str(e))
    except UnauthorizedError as e:
        return Probe(ProbeStatus.UNAUTHORIZED, 0, source, str(e))
    except ProviderError as e:
        return Probe(ProbeStatus.ERROR, 0, source, str(e))
    if not models:
        return Probe(
            ProbeStatus.EMPTY, 0, source, f"Reached {config.name}, but it lists no models."
        )
    return Probe(
        ProbeStatus.OK, len(models), source, f"Reached {config.name}: {len(models)} models."
    )


def autoconfigure() -> dict[str, ProviderConfig]:
    """The provider sections every launch makes sure of, in the panel's
    order: one per local engine, installed or not, its port and key
    detected from the machine; and one per cloud catalog whose key the
    shell carries, on its fixed endpoint with no key in the section —
    the variable is read at request time, never written, and setting
    it is the deliberate act that adds a catalog. What first run
    writes, and what a later launch founds where missing, so a catalog
    appears the first launch that finds its variable and stays. The
    generic provider is never founded: its url is nobody's to guess."""
    return {
        cls.id: cls.autoconfigure()
        for cls in ALL_CLIENTS.values()
        if cls.locality is Locality.LOCAL
        or (cls.locality is Locality.REMOTE and cls.env_key and os.environ.get(cls.env_key))
    }
