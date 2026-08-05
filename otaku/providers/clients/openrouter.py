"""OpenRouter: a hosted catalog over many upstream providers, speaking the
OpenAI protocol at https://openrouter.ai/api/v1. The base cloud listing
already harvests each model's `context_length` from the catalog."""

from otaku.providers.base import CloudClient
from otaku.settings.config import ProviderConfig


class OpenRouterClient(CloudClient):
    kind = "openrouter"

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # The deliberate-add default: the catalog's one endpoint; the api
        # key is the user's to provide.
        return ProviderConfig(name=cls.kind, url="https://openrouter.ai/api/v1")

    def balance(self, timeout: float = 10.0) -> str | None:
        # /credits reports lifetime purchases and spend, in dollars.
        data = self._get_json("/v1/credits", timeout=timeout)
        credits = data.get("data") if isinstance(data, dict) else None
        if not isinstance(credits, dict):
            return None
        total = credits.get("total_credits")
        used = credits.get("total_usage")
        if isinstance(total, int | float) and isinstance(used, int | float):
            return f"${total - used:.2f}"
        return None
