"""HTTP clients for OpenAI-compatible providers.

    ProviderClient        — generic OpenAI-compat fallback (catch-all)
    ├── OllamaClient      — /api/ps, /api/tags, /api/generate
    ├── LMStudioClient    — /api/v1/models[, /load, /unload]
    └── OmlxClient        — /v1/models/status[, /{id}/load, /{id}/unload]

Call `client_for(provider)` to get the right subclass — it probes the
URL and caches the result per provider name.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from otaku import config
from otaku.client.base import (
    ContentDelta,
    FinalStats,
    ProviderClient,
    ThinkingDelta,
)
from otaku.client.lmstudio import LMStudioClient
from otaku.client.ollama import OllamaClient
from otaku.client.omlx import OmlxClient
from otaku.config import Provider

_T = TypeVar("_T")

# Most-specific first; ProviderClient is the catch-all. Public because
# config.default_config() also walks it to assemble first-run provider sections.
PROBE_CHAIN: tuple[type[ProviderClient], ...] = (
    OllamaClient,
    LMStudioClient,
    OmlxClient,
    ProviderClient,
)
_client_cache: dict[str, ProviderClient] = {}


def client_for(provider: Provider) -> ProviderClient:
    """Return the most specific ProviderClient subclass for `provider`,
    cached per provider name."""
    if provider.name in _client_cache:
        return _client_cache[provider.name]
    for cls in PROBE_CHAIN:
        if cls.matches(provider):
            cli = cls(provider)
            _client_cache[provider.name] = cli
            return cli
    # Unreachable: ProviderClient.matches() is unconditionally True.
    raise RuntimeError(f"no client matched provider {provider.name!r}")


def map_providers(providers: Mapping[str, Provider], fn: Callable[[str, Provider], _T]) -> list[_T]:
    """Run `fn(name, provider)` for every configured provider concurrently,
    returning results in the mapping's iteration order.

    `fn` must handle its own errors — an exception it raises propagates out of
    this call. Concurrency is the point: one dead provider's probe/query timeout
    overlaps the others instead of adding to them, so a command's startup cost is
    the slowest single provider, not the sum across all of them.
    """
    items = list(providers.items())
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return list(pool.map(lambda item: fn(item[0], item[1]), items))


@contextlib.contextmanager
def probing_notice(providers: Mapping[str, Provider]) -> Iterator[None]:
    """Show a dim, transient "looking for providers" line on stderr while the
    body probes each provider (the concurrent probes take ~0.5s), then erase it.
    No-op when stderr isn't a TTY, so it never pollutes piped/redirected output."""
    active = sys.stderr.isatty()
    if active:
        names = ", ".join(providers) or "none configured"
        sys.stderr.write(
            f"\x1b[2mLooking for local providers ({names} — see {config.CONFIG_PATH})…\x1b[0m"
        )
        sys.stderr.flush()
    try:
        yield
    finally:
        if active:
            sys.stderr.write("\r\x1b[2K")  # return to column 0 + clear the line
            sys.stderr.flush()


def unreachable_help(providers: Mapping[str, Provider], reachable: set[str]) -> str:
    """Diagnostic printed when a command turns up no models: list every
    configured provider, whether it answered, and how to fix a dead config."""
    lines = [
        "No models reachable.",
        "",
        f"Checked these providers (configured in {config.CONFIG_PATH}):",
    ]
    for name in sorted(providers):
        if name in reachable:
            mark, note = "✓", "responding, but exposes no models"
        else:
            mark, note = "✗", "not responding — is the server running?"
        lines.append(f"  {mark} {name} → {providers[name].url}  ({note})")
    lines += [
        "",
        "Start your model server (e.g. `ollama serve`, or launch LM Studio), or edit",
        f"{config.CONFIG_PATH} to add or fix a [providers.NAME] `url`.",
    ]
    return "\n".join(lines)


__all__ = [
    "PROBE_CHAIN",
    "ContentDelta",
    "FinalStats",
    "ThinkingDelta",
    "client_for",
    "map_providers",
    "probing_notice",
    "unreachable_help",
]
