"""Provider lookup and fan-out.

The provider's section name selects its backend: "ollama", "omlx", and
"koboldcpp" get their native clients; any other name is served as a plain
OpenAI endpoint — which is all a remote or unknown server needs.

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
from otaku.providers.backends.koboldcpp import KoboldCppClient
from otaku.providers.backends.ollama import OllamaClient
from otaku.providers.backends.omlx import OmlxClient
from otaku.providers.base import ManagedClient, OpenAIClient
from otaku.settings.config import Provider

_T = TypeVar("_T")  # Registry.map's result type

# The backends with a native management API, by the provider name that
# activates them; every other name gets the plain OpenAIClient.
BACKENDS: dict[str, type[OpenAIClient]] = {
    OllamaClient.kind: OllamaClient,
    OmlxClient.kind: OmlxClient,
    KoboldCppClient.kind: KoboldCppClient,
}


def autoconfigure_providers() -> dict[str, Provider]:
    """The first-run provider sections: one per supported backend, present
    whether or not the engine is installed, each with its configuration
    (port, api key) detected from the machine. Runs only at the one
    first-run config write; the file is the user's thereafter."""
    configured = (
        OllamaClient.autoconfigure(),
        OmlxClient.autoconfigure(),
        KoboldCppClient.autoconfigure(),
    )
    return {provider.name: provider for provider in configured}


@dataclass(frozen=True)
class Model:
    """One model as a provider offers it."""

    name: str
    size: int | None = None  # bytes; None when the backend does not report it
    is_loaded: bool = False


@dataclass(frozen=True)
class Inventory:
    """One reachable provider's models, for the model picker."""

    provider: Provider
    models: builtins.list[Model]
    can_load_unload: bool


class Registry:
    def __init__(
        self,
        providers: dict[str, Provider],
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
        provider = self._providers.get(name)
        if provider is None:
            raise ValueError(f"no provider {name!r} in the configuration")
        cls = BACKENDS.get(name, OpenAIClient)
        client = cls(provider, request_log=self._request_log, smooth=self._smooth)
        self._clients[name] = client
        return client

    def resolve(self, spec: str) -> tuple[str, str]:
        """A model spec to (provider name, model). Accepts `provider/model`
        (the model part may itself contain slashes) and a bare model name,
        which every provider is asked about — a unique match wins. Raises
        ValueError with a human message otherwise."""
        if "/" in spec:
            head, _, rest = spec.partition("/")
            if head in self._providers and rest:
                return head, rest

        def probe(name: str, provider: Provider) -> str | None:
            try:
                models = self.get_client(name).list_models(timeout=3.0)
            except Exception:
                return None  # unreachable — skip, never fail the lookup
            return name if spec in set(models) else None

        matches = [name for name in self.map(probe) if name]
        if len(matches) == 1:
            return matches[0], spec
        known = ", ".join(sorted(self._providers))
        if not matches:
            raise ValueError(f"model {spec!r} not available in any configured provider ({known})")
        raise ValueError(
            f"model {spec!r} is in several providers ({', '.join(matches)}); "
            f"disambiguate as '<provider>/{spec}'"
        )

    def map(self, fn: Callable[[str, Provider], _T]) -> builtins.list[_T]:
        """Run `fn(name, provider)` for every configured provider
        concurrently, results in configuration order — one dead provider's
        timeout overlaps the others instead of adding to them. `fn` handles
        its own errors; an exception propagates."""
        items = list(self._providers.items())
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=len(items)) as pool:
            return list(pool.map(lambda item: fn(item[0], item[1]), items))

    def inventory(self) -> tuple[builtins.list[Inventory], set[str]]:
        """Every reachable provider's models, plus the reachable set — the
        model picker's one query. A provider that lists models but cannot
        report loaded state or sizes degrades to empty instead of dropping
        out."""

        def gather(name: str, provider: Provider) -> tuple[str, Inventory] | None:
            client = self.get_client(name)
            try:
                names = client.list_models(timeout=3.0)
            except Exception:
                return None
            try:
                loaded = client.get_loaded_models()
            except Exception:
                loaded = set()
            try:
                sizes = client.get_model_sizes()
            except Exception:
                sizes = {}
            # A loaded model missing from the listing still belongs in it.
            names += sorted(loaded - set(names))
            models = [Model(n, size=sizes.get(n), is_loaded=n in loaded) for n in names]
            row = Inventory(provider, models, isinstance(client, ManagedClient))
            return name, row

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
        lines.append("Start your model server, or fix the [providers.NAME] url in config.toml.")
        return "\n".join(lines)
