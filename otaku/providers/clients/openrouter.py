"""OpenRouter: a hosted catalog over many upstream providers, speaking
the OpenAI protocol at https://openrouter.ai/api/v1. The catalog names
each model's max context and the one its top provider serves it
with, its input modalities, and — in its `reasoning` object — the
efforts it takes and whether reasoning is mandatory; the key's own
endpoint answers a bad key with 401, which is how a key is checked,
the catalog itself being public. An effort goes out as
`reasoning_effort`. On Anthropic's models OpenRouter spends it as a
share of `max_tokens`, and a share under Anthropic's floor of 1024
thinking tokens is dropped: a small `max_tokens` turns the level off
there, not the level.
"""

from dataclasses import replace
from typing import Any

from otaku.formatting import Money
from otaku.providers.http import ASK_TIMEOUT, Http, positive_int
from otaku.providers.openai import reasoning
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import PROTOCOL_PARAMS, SAMPLER_PARAMS, OpenAICompletion
from otaku.providers.openai.models import ModelCapabilities, ModelInfo, OpenAIModels
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

    def verify_key(self, http: Http) -> None:
        # The key's own endpoint answers a bad key with 401; nothing
        # else is read of it here.
        http.get(f"{self._config.base_url}/v1/key")


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
            max_context_loaded=self._top_provider_int(top, "context_length"),
            max_output_tokens=self._top_provider_int(top, "max_completion_tokens"),
            capabilities=ModelCapabilities(
                vision="image" in modalities if isinstance(modalities, list) else None,
                audio="audio" in modalities if isinstance(modalities, list) else None,
                reasoning=self._reasoning_of(listed),
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

    def _reasoning_of(self, listed: dict[str, Any]) -> frozenset[str] | None:
        """The efforts a catalog model honours, as its `reasoning` object
        states them. `supported_efforts` names them; null, or omitted,
        means no selection among them — every effort reaches, as on:
        Gemma 4, whose object names none, spent reasoning tokens at high
        and none at none — and "none" reaches unless reasoning is
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
        named = spec.get("supported_efforts")
        efforts = reasoning.from_wire(named) if isinstance(named, list) else frozenset()
        efforts = efforts or reasoning.ALL_EFFORTS
        return efforts - {"none"} if spec.get("mandatory") else efforts | {"none"}

    def _top_provider_int(self, top: object, key: str) -> int | None:
        """A figure off the entry's `top_provider` object, the provider a
        request is routed to first."""
        return positive_int(top.get(key)) if isinstance(top, dict) else None


class OpenRouterCompletion(OpenAICompletion):
    # The wire takes every one; what reaches a given model is that
    # model's `supported_parameters`, a catalog fact left unread.
    supported_params = PROTOCOL_PARAMS | SAMPLER_PARAMS
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
        http = self._http.within(timeout)
        credits = self._account("/v1/credits", http)
        total = Money.of(credits.get("total_credits"))
        used = Money.of(credits.get("total_usage"))
        if total is None or used is None:
            return None
        left = Money(total.amount - used.amount, total.currency)
        cap = Money.of(self._account("/v1/key", http).get("limit_remaining"))
        if cap is not None and cap.amount < left.amount:
            return Money(cap.amount, left.currency)
        return left

    def _account(self, path: str, http: Http) -> dict[str, Any]:
        """The `data` object of an account endpoint, asked through the
        balance's view; {} when it will not say."""
        data = http.get(f"{self.config.base_url}{path}", quiet=True)
        inner = data.get("data") if isinstance(data, dict) else None
        return inner if isinstance(inner, dict) else {}
