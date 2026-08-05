"""Provider lookup and fan-out.

The provider's section name selects its backend — the single-model
engines ("llamacpp", "koboldcpp"), the local managed registries
("ollama", "omlx", "lmstudio"), and the cloud catalogs ("openrouter",
"nanogpt") each get their native client; any other name is served as a
plain OpenAI endpoint. First-run autoconfiguration writes sections for
the local backends only — a cloud provider is added deliberately, keys
and all.

The `Registry` is session-owned: the configured providers, the request
log, the smoothing flag, and the per-provider client cache — constructed
once by the CLI and passed explicitly. Nothing here ever blocks or exits
the app: an unreachable provider is skipped in fan-outs, and
`unreachable_help` is a message for callers to print, not a verdict.
"""

import builtins
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar

from otaku.logs.requests import RequestLog
from otaku.providers.base import ManagedClient, ModelInfo, OpenAIClient
from otaku.providers.clients.koboldcpp import KoboldCppClient
from otaku.providers.clients.llamacpp import LlamaCppClient
from otaku.providers.clients.lmstudio import LmStudioClient
from otaku.providers.clients.nanogpt import NanoGptClient
from otaku.providers.clients.ollama import OllamaClient
from otaku.providers.clients.omlx import OmlxClient
from otaku.providers.clients.openrouter import OpenRouterClient
from otaku.settings.config import ProviderConfig

_T = TypeVar("_T")  # Registry.map's result type

# The client classes with a native API, by the provider name that
# activates them, in the model picker's canonical order; every other
# name gets the plain OpenAIClient.
CLIENTS: dict[str, type[OpenAIClient]] = {
    LlamaCppClient.kind: LlamaCppClient,
    KoboldCppClient.kind: KoboldCppClient,
    OllamaClient.kind: OllamaClient,
    OmlxClient.kind: OmlxClient,
    LmStudioClient.kind: LmStudioClient,
    OpenRouterClient.kind: OpenRouterClient,
    NanoGptClient.kind: NanoGptClient,
}


@dataclass(frozen=True)
class Inventory:
    """One reachable provider's models, for the model picker."""

    provider_config: ProviderConfig
    models: builtins.list[ModelInfo]
    can_load_unload: bool


class Registry:
    def __init__(
        self,
        providers: dict[str, ProviderConfig],
        *,
        request_log: RequestLog | None = None,
        smooth: bool = True,
    ) -> None:
        self._providers = providers
        self._request_log = request_log
        self._smooth = smooth
        self._clients: dict[str, OpenAIClient] = {}

    def get_client(self, name: str) -> OpenAIClient:
        """The named provider's client, cached — its backend chosen by the
        name (see the module docstring)."""
        if name in self._clients:
            return self._clients[name]
        provider_config = self._providers.get(name)
        if provider_config is None:
            raise ValueError(f"no provider {name!r} in the configuration")
        cls = CLIENTS.get(name, OpenAIClient)
        client = cls(provider_config, request_log=self._request_log, smooth=self._smooth)
        self._clients[name] = client
        return client

    def configured(self) -> builtins.list[ProviderConfig]:
        """Every configured provider, name-sorted — reachable or not; the
        provider panel edits them all."""
        return [self._providers[name] for name in sorted(self._providers)]

    def update_provider(self, provider_config: ProviderConfig) -> None:
        """Swap one provider's configuration for the running session and
        drop its cached client, so the next request is built against the
        new url and key. Persisting the change is the caller's business."""
        self._providers[provider_config.name] = provider_config
        self._clients.pop(provider_config.name, None)

    def map(self, fn: Callable[[str, ProviderConfig], _T]) -> builtins.list[_T]:
        """Run `fn(name, provider_config)` for every configured provider
        concurrently, results in configuration order — one dead provider's
        timeout overlaps the others instead of adding to them. `fn` handles
        its own errors; an exception propagates."""
        items = list(self._providers.items())
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=len(items)) as pool:
            return list(pool.map(lambda item: fn(item[0], item[1]), items))

    def inventory(self, skip: set[str] | None = None) -> tuple[builtins.list[Inventory], set[str]]:
        """Every reachable provider's models, plus the reachable set — the
        model picker's one query, each backend answering with its rich
        rows in one pass. `skip` names providers to leave out: the picker
        opens on the local engines' answers and fetches the cloud
        catalogs asynchronously, after the screen is up."""

        # Inner on purpose: the filter closes over the skip set.
        def gather(name: str, provider_config: ProviderConfig) -> tuple[str, Inventory] | None:
            if skip and name in skip:
                return None
            return self._gather(name, provider_config)

        results = [r for r in self.map(gather) if r is not None]
        return [row for _name, row in results], {name for name, _row in results}

    def unreachable_help(self, reachable: set[str]) -> str:
        """A diagnostic for a command that found no models: every configured
        provider, whether it answered, and where to fix it. Informational
        only — printing it is the caller's choice, and nothing exits."""
        lines = ["No models reachable right now."]
        for name in sorted(self._providers):
            if name in reachable:
                mark, note = "+", "responding, but exposes no models"
            else:
                mark, note = "x", "not responding — is the server running?"
            lines.append(f"  {mark} {name} → {self._providers[name].url}  ({note})")
        lines.append("Start your model server, or fix the [NAME] url in providers.toml.")
        return "\n".join(lines)

    def _gather(self, name: str, provider_config: ProviderConfig) -> tuple[str, Inventory] | None:
        """One provider's inventory row, or None when it is unreachable."""
        client = self.get_client(name)
        try:
            models = client.models(timeout=5.0)
        except Exception:
            return None
        return name, Inventory(provider_config, models, isinstance(client, ManagedClient))


def autoconfigure_providers() -> dict[str, ProviderConfig]:
    """The first-run provider sections: one per supported backend, present
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
    return {provider_config.name: provider_config for provider_config in configured}
