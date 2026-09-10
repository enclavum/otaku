"""NanoGPT: a hosted catalog speaking the OpenAI protocol at
https://nano-gpt.com/api/v1. The detailed listing names each model's
context window, its capabilities (vision and reasoning among them) and
the efforts it takes; the account's balance answers only a working
key, which is how a key is checked.
"""

from typing import Any, ClassVar

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
from otaku.providers.wire import ALL_THINKING_LEVELS, THINKING_EFFORT_KNOB
from otaku.settings.providers import ProviderConfig


class NanoGptClient(Client):
    kind = "nanogpt"
    label = "NanoGPT"
    locality = Locality.REMOTE
    env_key = "NANOGPT_API_KEY"
    # The effort is honoured on the raw wire too: a level makes the
    # model think, inline in the text.
    text_thinking_knobs: ClassVar[frozenset[str]] = frozenset({THINKING_EFFORT_KNOB})

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # The deliberate-add default: the catalog's one endpoint; the api
        # key is the user's to provide.
        return ProviderConfig(name=cls.kind, url="https://nano-gpt.com/api/v1")

    def balance(self, timeout: float = ASK_TIMEOUT) -> Money | None:
        # The account balance lives on the legacy /api surface. Only the
        # dollar figure is reported — the crypto balances riding along in
        # the same payload are not otaku's business.
        data = http.post_json(
            f"{self.config.base_url}/check-balance",
            {},
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
            quiet=True,
        )
        if not isinstance(data, dict) or "usd_balance" not in data:
            return None
        return Money.of(data["usd_balance"], "USD")

    def models(self, timeout: float = ASK_TIMEOUT) -> list[ModelInfo]:
        # The plain listing hides the model details; the flag adds each
        # model's context window and capabilities to the rows.
        return self._full_models(timeout, query="?detailed=true", keyed=True)

    def _decode_capabilities(self, listed: dict[str, Any]) -> Capabilities | None:
        caps = listed.get("capabilities")
        if not isinstance(caps, dict):
            return None
        if not caps.get("reasoning", True):
            thinking: frozenset[str] = frozenset()
        else:
            # The efforts it lists, and off — a model that reasons but
            # names no efforts leaves the question open.
            efforts = wire.effort_levels(listed.get("reasoning_efforts") or [])
            thinking = (efforts | {"off"}) if efforts else ALL_THINKING_LEVELS
        return Capabilities(vision=bool(caps.get("vision", True)), thinking=thinking)

    def _context_size(self, model: str) -> int | None:
        # The catalog is the one source: a listing seeds every size.
        self.models(timeout=LISTING_TIMEOUT)
        return self._context_sizes.get(model)
