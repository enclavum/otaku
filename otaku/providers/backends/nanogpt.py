"""NanoGPT: a hosted catalog speaking the OpenAI protocol at
https://nano-gpt.com/api/v1. The base cloud listing serves it as is —
context windows appear whenever the catalog reports `context_length`."""

from otaku.providers.base import CloudClient
from otaku.settings.config import Provider


class NanoGptClient(CloudClient):
    kind = "nanogpt"
    # The plain listing hides the model details; the flag adds each
    # model's context_length to the catalog rows.
    _MODELS_QUERY = "?detailed=true"

    @classmethod
    def autoconfigure(cls) -> Provider:
        # The deliberate-add default: the catalog's one endpoint; the api
        # key is the user's to provide.
        return Provider(name=cls.kind, url="https://nano-gpt.com/api/v1")

    def balance(self, timeout: float = 10.0) -> str | None:
        # The account balance lives on the legacy /api surface. Only the
        # dollar figure is reported — the crypto balances riding along in
        # the same payload are not otaku's business.
        data = self._post_json("/check-balance", {}, timeout=timeout)
        if not isinstance(data, dict):
            return None
        try:
            return f"${float(data['usd_balance']):.2f}"
        except (KeyError, TypeError, ValueError):
            return None
