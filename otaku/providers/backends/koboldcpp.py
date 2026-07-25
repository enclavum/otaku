"""KoboldCpp: one model per process, chosen at launch — no load/unload.
The subclass reports the single always-loaded model and reads the true
context window; chat rides the OpenAI protocol at /v1."""

from otaku.providers.base import OpenAIClient
from otaku.settings.config import Provider


class KoboldCppClient(OpenAIClient):
    kind = "koboldcpp"

    @classmethod
    def autoconfigure(cls) -> Provider:
        # Configured by launch flags — nothing on disk to detect a port
        # from, so the section is the standard default.
        return Provider(name=cls.kind, url="http://localhost:5001/v1")

    def get_loaded_models(self, timeout: float = 1.5) -> set[str]:
        # "inactive" is what admin mode reports between swaps.
        data = self._get_json("/api/v1/model", timeout=timeout)
        if isinstance(data, dict):
            model = data.get("result")
            if isinstance(model, str) and model and model != "inactive":
                return {model}
        return set()

    def _fetch_context_size(self, model: str) -> int | None:
        data = self._get_json("/api/extra/true_max_context_length", timeout=1.5)
        if isinstance(data, dict):
            value = data.get("value")
            if isinstance(value, int) and value > 0:
                return value
        return None
