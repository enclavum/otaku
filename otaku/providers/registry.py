"""Provider lookup and fan-out — and the probe that asks a provider
before it is saved.

The section name selects the engine: `CLIENTS` maps the eight names to
their clients in the model picker's canonical order — the generic
provider first, the engines on this machine, the catalogs — and a
section named anything else is a hand-written one, served by the
generic client over the protocol alone. First-run autoconfiguration
writes sections for the local engines only; the generic provider and a
cloud one are added deliberately, url and key and all.

The `Registry` is composed by the backend package and injected
everywhere a client is resolved: the configured providers, the
request-log sink, the smoothing flag, and the per-provider client
cache. File persistence is NOT here — the panel saves write through
settings and then call `update_provider`. Nothing here ever blocks or
exits the app: an unreachable provider is skipped in fan-outs, and a
dead one costs its own timeout — overlapped with the others, never
the launch.
"""

import enum
from collections.abc import Callable, Collection, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar

from otaku.providers.client import (
    LISTING_TIMEOUT,
    Client,
    KeySource,
    Locality,
    ModelInfo,
    RequestSink,
)
from otaku.providers.clients.generic import OpenAIClient
from otaku.providers.clients.koboldcpp import KoboldCppClient
from otaku.providers.clients.llamacpp import LlamaCppClient
from otaku.providers.clients.lmstudio import LmStudioClient
from otaku.providers.clients.nanogpt import NanoGptClient
from otaku.providers.clients.ollama import OllamaClient
from otaku.providers.clients.omlx import OmlxClient
from otaku.providers.clients.openrouter import OpenRouterClient
from otaku.providers.errors import ProviderError, UnauthorizedError, UnreachableError
from otaku.settings.providers import ProviderConfig

_T = TypeVar("_T")  # Registry.map's result type

CLIENTS: dict[str, type[Client]] = {
    OpenAIClient.kind: OpenAIClient,
    LlamaCppClient.kind: LlamaCppClient,
    KoboldCppClient.kind: KoboldCppClient,
    OllamaClient.kind: OllamaClient,
    OmlxClient.kind: OmlxClient,
    LmStudioClient.kind: LmStudioClient,
    OpenRouterClient.kind: OpenRouterClient,
    NanoGptClient.kind: NanoGptClient,
}


@dataclass(frozen=True)
class ProviderInfo:
    """One reachable provider with its models — what `Registry.info`
    answers and the model picker lists."""

    config: ProviderConfig
    models: list[ModelInfo]
    can_load_unload: bool
    locality: Locality  # not LOCAL: nothing to size, no load state


class ProbeOutcome(enum.Enum):
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

    outcome: ProbeOutcome
    models_count: int
    key_source: KeySource | None
    message: str


class Registry:
    def __init__(
        self,
        configs: dict[str, ProviderConfig],
        *,
        request_sink: RequestSink | None = None,
        smooth: bool = True,
    ) -> None:
        self.configs = configs
        self._request_sink = request_sink
        self._smooth = smooth
        self._clients: dict[str, Client] = {}

    def get_client(self, provider: str) -> Client:
        """The named provider's client, cached — its engine chosen by the
        name (see the module docstring). Raises ValueError for a provider
        that is not configured."""
        if provider in self._clients:
            return self._clients[provider]
        config = self.configs.get(provider)
        if config is None:
            raise ValueError(f"no provider {provider!r} in the configuration")
        # An engine's own client by the section's name, the generic one
        # for a hand-written section.
        cls = CLIENTS.get(provider, OpenAIClient)
        client = cls(config, request_sink=self._request_sink, smooth=self._smooth)
        self._clients[provider] = client
        return client

    def names(self) -> list[str]:
        """The configured providers' names, sorted."""
        return sorted(self.configs)

    def update(self, config: ProviderConfig) -> None:
        """Swap one provider's configuration for the running session and
        drop its cached client, so the next request is built against the
        new url and key. Persisting the change is the caller's business."""
        self.configs[config.name] = config
        self._clients.pop(config.name, None)

    def key_source(self, provider: str) -> KeySource | None:
        """Where the key the named provider sends comes from, None for
        no key — what a panel says beside an emptied key field. Raises
        ValueError as `get_client` does."""
        return self.get_client(provider).key_source

    def map(
        self, fn: Callable[[str, ProviderConfig], _T], names: Iterable[str] | None = None
    ) -> list[_T]:
        """Run `fn(provider, config)` for every configured provider — or
        the `names` given — concurrently, results in that order: one
        dead provider's timeout overlaps the others instead of adding to
        them. `fn` handles its own errors; an exception propagates."""
        items = [(name, self.configs[name]) for name in (self.configs if names is None else names)]
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=len(items)) as pool:
            return list(pool.map(lambda item: fn(item[0], item[1]), items))

    def info(self, skip_providers: Collection[str] = ()) -> list[ProviderInfo]:
        """Every reachable provider with its models — the model picker's
        one query, each engine answering with its rich rows in one pass;
        a provider that cannot answer is absent. `skip_providers` are
        not even asked: the picker opens on the local engines' answers
        and fetches the cloud catalogs after the screen is up."""
        asked = [name for name in self.names() if name not in skip_providers]
        return [row for row in self.map(self._info_of, asked) if row is not None]

    def _info_of(self, provider: str, config: ProviderConfig) -> ProviderInfo | None:
        """One provider's row, or None when it cannot answer — a dead
        server or a rejected key."""
        client = self.get_client(provider)
        try:
            models = client.models(timeout=LISTING_TIMEOUT)
        except ProviderError:
            return None
        return ProviderInfo(config, models, client.manages_models, client.locality)


def probe(config: ProviderConfig, *, timeout: float = LISTING_TIMEOUT) -> Probe:
    """Ask the provider `config` describes — saved or not — whether it
    answers and what it lists, the way the panel wants to know before
    Save. Never raises: unreachable, a rejected key, an error status and
    an empty listing are ANSWERS, each with the sentence that says it."""
    client = CLIENTS.get(config.name, OpenAIClient)(config)
    try:
        rows = client.models(timeout)
    except UnreachableError as e:
        return Probe(ProbeOutcome.UNREACHABLE, 0, client.key_source, str(e))
    except UnauthorizedError as e:
        return Probe(ProbeOutcome.UNAUTHORIZED, 0, client.key_source, str(e))
    except ProviderError as e:
        return Probe(ProbeOutcome.ERROR, 0, client.key_source, str(e))
    if not rows:
        message = f"Reached {config.name}, but it lists no models."
        return Probe(ProbeOutcome.EMPTY, 0, client.key_source, message)
    message = f"Reached {config.name}: {len(rows)} models."
    return Probe(ProbeOutcome.OK, len(rows), client.key_source, message)


def autoconfigure() -> dict[str, ProviderConfig]:
    """The first-run provider sections: one per local engine, present
    whether or not the engine is installed, each with its configuration
    (port, api key) detected from the machine. Runs only at the one
    first-run config write; the file is the user's thereafter."""
    configured = (
        LlamaCppClient.autoconfigure(),
        KoboldCppClient.autoconfigure(),
        OllamaClient.autoconfigure(),
        OmlxClient.autoconfigure(),
        LmStudioClient.autoconfigure(),
    )
    return {config.name: config for config in configured}
