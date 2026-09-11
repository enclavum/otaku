"""NanoGPT: a hosted catalog speaking the OpenAI protocol at
https://nano-gpt.com/api/v1. The detailed listing names each model's
context window — the model's own; no serving limit is stated — its
capabilities (vision and reasoning among them) and the efforts it
takes; the account's balance answers only a working key, which is how
a key is checked.
"""

from dataclasses import replace
from typing import Any, ClassVar

from otaku.formatting import Money
from otaku.providers import http
from otaku.providers.http import ASK_TIMEOUT
from otaku.providers.openai import reasoning
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion
from otaku.providers.openai.models import Capabilities, ModelInfo, OpenAIModels
from otaku.settings.providers import ProviderConfig


class NanoGptAuth(OpenAIAuth):
    def verify_key(self, timeout: float) -> None:
        # The account endpoint answers a bad key with 401; nothing else
        # is read of it here.
        http.post_json(
            f"{self._config.base_url}/check-balance",
            {},
            name=self._config.name,
            headers=self.headers,
            timeout=timeout,
        )


class NanoGptModels(OpenAIModels):
    listing_keyed = True
    # The plain listing hides the model details; the flag adds each
    # model's context window and capabilities to the entries.
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
            # The efforts it lists, and none — a model that reasons but
            # names no efforts leaves the question open.
            efforts = reasoning.from_wire(listed.get("reasoning_efforts") or [])
            honoured = (efforts | {"none"}) if efforts else None
        return replace(
            model,
            capabilities=Capabilities(
                vision=bool(caps["vision"]) if "vision" in caps else None,
                reasoning=honoured,
                text_completion=True,
            ),
        )


class NanoGptCompletion(OpenAICompletion):
    # The effort is honoured on the raw wire too: it makes the
    # model reason, inline in the text.
    text_reasoning_knobs: ClassVar[frozenset[str]] = frozenset({reasoning.EFFORT_KNOB})


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
        data = http.post_json(
            f"{self.config.base_url}/check-balance",
            {},
            name=self.config.name,
            headers=self.auth.headers,
            timeout=timeout,
            quiet=True,
        )
        if not isinstance(data, dict) or "usd_balance" not in data:
            return None
        return Money.of(data["usd_balance"], "USD")
