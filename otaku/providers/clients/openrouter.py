"""OpenRouter: a hosted catalog over many upstream providers, speaking
the OpenAI protocol at https://openrouter.ai/api/v1. The catalog names
each model's context window and the one its top provider serves it
with, its input modalities, and — in its `reasoning` object — the
efforts it takes and whether reasoning is mandatory; the account's
balance answers only a working key, which is how a key is checked, the
catalog itself being public.
"""

from dataclasses import replace
from typing import Any

from otaku.formatting import Money
from otaku.providers import http
from otaku.providers.http import ASK_TIMEOUT
from otaku.providers.openai import reasoning
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion
from otaku.providers.openai.models import Capabilities, ModelInfo, OpenAIModels
from otaku.settings.providers import ProviderConfig

# OpenRouter attributes a request to the app that sent it, by three
# headers it reads and nobody else does — the referer is the app's
# identity (without it there is no app at all), the title is how it is
# named, and the categories are which shelves it is browsed from. They
# say WHICH APP, never which user, and go to OpenRouter alone.
#
# The categories must come from OpenRouter's own vocabulary or they are
# dropped in silence, and two is the documented maximum per request.
_ATTRIBUTION = {
    "HTTP-Referer": "https://otaku.sh",
    "X-OpenRouter-Title": "otaku",
    "X-OpenRouter-Categories": "roleplay,creative-writing",
}


class OpenRouterAuth(OpenAIAuth):
    @property
    def headers(self) -> dict[str, str]:
        return {**super().headers, **_ATTRIBUTION}

    def verify_key(self, timeout: float) -> None:
        # The account endpoint answers a bad key with 401; nothing else
        # is read of it here.
        http.get_json(
            f"{self._config.base_url}/v1/credits",
            name=self._config.name,
            headers=self.headers,
            timeout=timeout,
        )


class OpenRouterModels(OpenAIModels):
    listing_keyed = True

    def _decode(self, listed: dict[str, Any], model: ModelInfo) -> ModelInfo:
        architecture = listed.get("architecture")
        modalities = (
            architecture.get("input_modalities") if isinstance(architecture, dict) else None
        )
        parameters = listed.get("supported_parameters")
        top = listed.get("top_provider")
        return replace(
            model,
            max_context_loaded=_top_provider_int(top, "context_length"),
            max_output_tokens=_top_provider_int(top, "max_completion_tokens"),
            capabilities=Capabilities(
                vision="image" in modalities if isinstance(modalities, list) else None,
                audio="audio" in modalities if isinstance(modalities, list) else None,
                reasoning=_reasoning_of(listed),
                text_completion=True,
                structured_output=(
                    "structured_outputs" in parameters or "response_format" in parameters
                    if isinstance(parameters, list)
                    else None
                ),
            ),
        )


class OpenRouterCompletion(OpenAICompletion):
    # OpenRouter forwards `cache_control` breakpoints to the providers
    # that honour them (Anthropic above all) and drops them elsewhere —
    # marking is safe across the whole catalog.
    can_mark_cache = True
    # No text knob: on the raw wire an effort derails the model (Gemma 4
    # answered a puzzle with a page of "Good,") and no reasoning comes.


class OpenRouterClient(OpenAIClient):
    id = "openrouter"
    label = "OpenRouter"
    locality = Locality.REMOTE
    env_key = "OPENROUTER_API_KEY"
    auth_class = OpenRouterAuth
    models_class = OpenRouterModels
    completion_class = OpenRouterCompletion

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # The deliberate-add default: the catalog's one endpoint; the api
        # key is the user's to provide.
        return ProviderConfig(name=cls.id, url="https://openrouter.ai/api/v1")

    def balance(self, timeout: float = ASK_TIMEOUT) -> Money | None:
        # /credits reports lifetime purchases and spend, in dollars.
        data = http.get_json(
            f"{self.config.base_url}/v1/credits",
            name=self.config.name,
            headers=self.auth.headers,
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


def _reasoning_of(listed: dict[str, Any]) -> frozenset[str] | None:
    """The efforts a catalog model honours: its `reasoning` object names
    them and says whether reasoning is mandatory, in which case "none" is
    not among them. An object that names none (Gemma 4's) still takes
    every effort — it becomes a budget there — so it leaves them all. A
    model without one that does not take the reasoning parameter cannot
    reason at all; one that says neither cannot be read."""
    spec = listed.get("reasoning")
    if isinstance(spec, dict):
        efforts = reasoning.from_wire(spec.get("supported_efforts") or []) or reasoning.ALL_EFFORTS
        return efforts - {"none"} if spec.get("mandatory") else efforts | {"none"}
    parameters = listed.get("supported_parameters")
    if isinstance(parameters, list):
        return frozenset() if "reasoning" not in parameters else reasoning.ALL_EFFORTS
    return None


def _top_provider_int(top: object, key: str) -> int | None:
    """A figure off the entry's `top_provider` object, the provider a
    request is routed to first."""
    return http.positive_int(top.get(key)) if isinstance(top, dict) else None
