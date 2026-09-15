"""NanoGPT: a hosted catalog speaking the OpenAI protocol at
https://nano-gpt.com/api/v1. The detailed listing names each model's
max context — the model's own; no serving limit is stated — its
output limit, its capabilities (vision, audio, reasoning, structured
output) and the efforts it takes, "none" among them only where the
model takes it. The account lives on the legacy /api surface at the
url's origin: its balance answers only a working key, which is how a
key is checked. Cache markers are forwarded to the models that honour
them and dropped elsewhere, as on OpenRouter.
"""

from dataclasses import replace
from typing import Any, ClassVar
from urllib.parse import urlsplit

from otaku.formatting import Money
from otaku.providers.http import ASK_TIMEOUT, Http, positive_int
from otaku.providers.openai import frames, reasoning
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion
from otaku.providers.openai.models import Capabilities, ModelInfo, OpenAIModels
from otaku.settings.providers import ProviderConfig


class NanoGptAuth(OpenAIAuth):
    def verify_key(self, http: Http) -> None:
        # The account endpoint answers a bad key with 401; nothing else
        # is read of it here.
        http.post(_account_url(self._config.url), {})


class NanoGptModels(OpenAIModels):
    listing_keyed = True
    # The plain listing hides the model details; the flag adds each
    # model's max context and capabilities to the entries.
    listing_query = "?detailed=true"

    def _decode(self, listed: dict[str, Any], model: ModelInfo) -> ModelInfo:
        caps = listed.get("capabilities")
        if not isinstance(caps, dict):
            return model
        honoured: frozenset[str] | None
        if "reasoning" not in caps:
            honoured = None
        elif not caps["reasoning"]:
            honoured = frozenset()
        else:
            # The efforts it lists, as listed: "none" is among them only
            # where the model takes it. A model that reasons but names
            # no efforts leaves the question open.
            honoured = reasoning.from_wire(listed.get("reasoning_efforts") or []) or None
        return replace(
            model,
            max_output_tokens=positive_int(listed.get("max_output_tokens")),
            capabilities=Capabilities(
                vision=self._flag(caps, "vision"),
                audio=self._flag(caps, "audio_input"),
                reasoning=honoured,
                # The completions endpoint is best-effort and per model,
                # and the catalog does not say which take it.
                text_completion=None,
                structured_output=self._flag(caps, "structured_output"),
            ),
        )

    def _flag(self, caps: dict[str, Any], key: str) -> bool | None:
        return bool(caps[key]) if key in caps else None


class NanoGptCompletion(OpenAICompletion):
    # Inline `cache_control` reaches the models that honour it and is
    # dropped elsewhere: marking is safe across the catalog.
    can_mark_cache = True
    # The effort is accepted on the raw wire too, and never derails a
    # completion; what a model does with it there is the model's own.
    text_reasoning_knobs: ClassVar[frozenset[str]] = frozenset({reasoning.EFFORT_KNOB})

    def _read_usage(self, event: dict[str, Any]) -> frames.Usage | None:
        # The text wire reports no `usage`; its counts ride the pricing block.
        report = frames.read_usage(event)
        if report is not None:
            return report
        pricing = event.get("x_nanogpt_pricing")
        if not isinstance(pricing, dict):
            return None
        counts = (
            positive_int(pricing.get("inputTokens")),
            positive_int(pricing.get("outputTokens")),
        )
        return (*counts, None) if any(count is not None for count in counts) else None


class NanoGptClient(OpenAIClient):
    id = "nanogpt"
    label = "NanoGPT"
    locality = Locality.REMOTE
    env_key = "NANOGPT_API_KEY"
    auth_class = NanoGptAuth
    models_class = NanoGptModels
    completion_class = NanoGptCompletion

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # The deliberate-add default: the catalog's one endpoint; the api
        # key is the user's to provide.
        return ProviderConfig(name=cls.id, url="https://nano-gpt.com/api/v1")

    def balance(self, timeout: float = ASK_TIMEOUT) -> Money | None:
        # The account balance lives on the legacy /api surface. Only the
        # dollar figure is reported — the crypto balances riding along in
        # the same payload are not otaku's business.
        data = self._http.post(_account_url(self.config.url), {}, timeout=timeout, quiet=True)
        if not isinstance(data, dict) or "usd_balance" not in data:
            return None
        return Money.of(data["usd_balance"], "USD")


def _account_url(url: str) -> str:
    """The balance endpoint, at the url's origin: it lives on the legacy
    /api surface, wherever the section's path points (`/api/v1`, or
    the `/v1` shorthand the host also serves)."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/api/check-balance"
