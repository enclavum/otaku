"""OpenRouter: a hosted catalog over many upstream providers, speaking
the OpenAI protocol at https://openrouter.ai/api/v1. The catalog names
each model's context window, its input modalities, and — in its
`reasoning` object — the efforts it takes and whether reasoning is
mandatory; the account's balance answers only a working key, which is
how a key is checked, the catalog itself being public.
"""

from typing import Any

from otaku.formatting import Money
from otaku.providers import http, wire
from otaku.providers.client import (
    ASK_TIMEOUT,
    LISTING_TIMEOUT,
    Capabilities,
    Client,
    Locality,
    ModelInfo,
)
from otaku.providers.wire import ALL_THINKING_LEVELS
from otaku.settings.providers import ProviderConfig

# OpenRouter attributes a request to the app that sent it, by three
# headers it reads and nobody else does — the referer is the app's
# identity (without it there is no app at all), the title is how it is
# named, and the categories are which shelves it is browsed from. They
# say WHICH APP, never which user, and go to OpenRouter alone: the
# `_headers` hook is per client.
#
# The categories must come from OpenRouter's own vocabulary or they are
# dropped in silence, and two is the documented maximum per request —
# these are those two.
_ATTRIBUTION = {
    "HTTP-Referer": "https://otaku.sh",
    "X-OpenRouter-Title": "otaku",
    "X-OpenRouter-Categories": "roleplay,creative-writing",
}


class OpenRouterClient(Client):
    kind = "openrouter"
    label = "OpenRouter"
    locality = Locality.REMOTE
    env_key = "OPENROUTER_API_KEY"
    # OpenRouter forwards `cache_control` breakpoints to the providers
    # that honour them (Anthropic above all) and drops them elsewhere —
    # marking is safe across the whole catalog.
    cache_markers = True
    # No text knob: on the raw wire an effort derails the model (Gemma 4
    # answered a puzzle with a page of "Good,") and no reasoning comes.

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # The deliberate-add default: the catalog's one endpoint; the api
        # key is the user's to provide.
        return ProviderConfig(name=cls.kind, url="https://openrouter.ai/api/v1")

    def balance(self, timeout: float = ASK_TIMEOUT) -> Money | None:
        # /credits reports lifetime purchases and spend, in dollars.
        data = http.get_json(
            f"{self.config.base_url}/v1/credits",
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
            quiet=True,
        )
        credits = data.get("data") if isinstance(data, dict) else None
        if not isinstance(credits, dict):
            return None
        total = Money.of(credits.get("total_credits"))
        used = Money.of(credits.get("total_usage"))
        if total is None or used is None:
            return None
        # What is LEFT: purchased minus spent, in the currency both are in.
        return Money(total.amount - used.amount, total.currency)

    @property
    def _headers(self) -> dict[str, str]:
        return {**super()._headers, **_ATTRIBUTION}

    def models(self, timeout: float = ASK_TIMEOUT) -> list[ModelInfo]:
        return self._full_models(timeout, keyed=True)

    def _decode_capabilities(self, listed: dict[str, Any]) -> Capabilities:
        architecture = listed.get("architecture")
        modalities = (
            architecture.get("input_modalities") if isinstance(architecture, dict) else None
        )
        vision = "image" in modalities if isinstance(modalities, list) else True
        return Capabilities(vision=vision, thinking=_thinking_of(listed))

    def _context_size(self, model: str) -> int | None:
        # The catalog is the one source: a listing seeds every size.
        self.models(timeout=LISTING_TIMEOUT)
        return self._context_sizes.get(model)


def _thinking_of(listed: dict[str, Any]) -> frozenset[str]:
    """The levels a catalog row honours: its `reasoning` object names
    the efforts and says whether reasoning is mandatory, in which case
    "off" is not among them. An object that names no efforts (Gemma 4's)
    still takes the levels — the effort becomes a budget there — so it
    leaves them all. A row without one that does not take the reasoning
    parameter cannot think at all."""
    reasoning = listed.get("reasoning")
    if isinstance(reasoning, dict):
        levels = wire.effort_levels(reasoning.get("supported_efforts") or []) or ALL_THINKING_LEVELS
        return levels - {"off"} if reasoning.get("mandatory") else levels | {"off"}
    parameters = listed.get("supported_parameters")
    if isinstance(parameters, list) and "reasoning" not in parameters:
        return frozenset()
    return ALL_THINKING_LEVELS
