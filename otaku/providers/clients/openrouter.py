"""OpenRouter: a hosted catalog over many upstream providers, speaking
the OpenAI protocol at https://openrouter.ai/api/v1. The catalog names
each model's max context and the one its top provider serves it
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
                # The completions endpoint is per model and the catalog
                # does not say which take it.
                text_completion=None,
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
        # /credits reports the account's lifetime purchases and spend, in
        # dollars; /key what this key may still spend, when it is capped
        # below that. What is left is the tighter of the two.
        credits = self._account("/v1/credits", timeout)
        total = Money.of(credits.get("total_credits"))
        used = Money.of(credits.get("total_usage"))
        if total is None or used is None:
            return None
        left = Money(total.amount - used.amount, total.currency)
        cap = Money.of(self._account("/v1/key", timeout).get("limit_remaining"))
        if cap is not None and cap.amount < left.amount:
            return Money(cap.amount, left.currency)
        return left

    def _account(self, path: str, timeout: float) -> dict[str, Any]:
        """The `data` object of an account endpoint, {} when it will not say."""
        data = http.get_json(
            f"{self.config.base_url}{path}",
            name=self.config.name,
            headers=self.auth.headers,
            timeout=timeout,
            quiet=True,
        )
        inner = data.get("data") if isinstance(data, dict) else None
        return inner if isinstance(inner, dict) else {}


def _reasoning_of(listed: dict[str, Any]) -> frozenset[str] | None:
    """The efforts a catalog model honours, as its `reasoning` object
    states them. `supported_efforts` names them; null means every
    effort; OMITTED means the model exposes no effort selection, so
    only off reaches it — "none" alone, and nothing when reasoning is
    mandatory. A model whose parameters do not take `reasoning` cannot
    reason, object or not; one that says neither cannot be read."""
    parameters = listed.get("supported_parameters")
    can_reason = isinstance(parameters, list) and "reasoning" in parameters
    spec = listed.get("reasoning")
    if not isinstance(spec, dict):
        if not isinstance(parameters, list):
            return None
        return reasoning.ALL_EFFORTS if can_reason else frozenset()
    if not can_reason:
        return frozenset()
    mandatory = bool(spec.get("mandatory"))
    if "supported_efforts" not in spec:
        return frozenset() if mandatory else frozenset({"none"})
    efforts = reasoning.from_wire(spec.get("supported_efforts") or []) or reasoning.ALL_EFFORTS
    return efforts - {"none"} if mandatory else efforts | {"none"}


def _top_provider_int(top: object, key: str) -> int | None:
    """A figure off the entry's `top_provider` object, the provider a
    request is routed to first."""
    return http.positive_int(top.get(key)) if isinstance(top, dict) else None
