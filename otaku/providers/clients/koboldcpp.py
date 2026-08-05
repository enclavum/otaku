"""KoboldCpp: one model per process, chosen at launch — no load/unload.
The subclass refines the single-model listing with admin-mode's active
state and reads the true context window; chat rides the OpenAI protocol
at /v1."""

from otaku.providers.base import LocalSingleClient, ModelInfo
from otaku.settings.config import ProviderConfig


class KoboldCppClient(LocalSingleClient):
    kind = "koboldcpp"
    supports_thinking = False  # no request-level knob; thinking is model-baked

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # Configured by launch flags — nothing on disk to detect a port
        # from, so the section is the standard default.
        return ProviderConfig(name=cls.kind, url="http://localhost:5001/v1")

    def _list(self, timeout: float) -> list[ModelInfo]:
        """The single-model listing, the engine's own name prefix
        stripped; admin mode's active state refines `loaded` when that
        surface answers."""
        active = self._active_model(timeout=1.5)
        return [
            ModelInfo(
                name=name,
                context=self.get_context_size(name),
                loaded=name == active if active is not None else True,
            )
            for name in (_bare(raw) for raw in self._model_names(timeout))
        ]

    def _active_model(self, timeout: float) -> str | None:
        """The model admin mode reports as active; "" between swaps
        ("inactive"), None when the endpoint does not answer."""
        data = self._get_json("/api/v1/model", timeout=timeout)
        if isinstance(data, dict):
            model = data.get("result")
            if isinstance(model, str):
                return "" if model == "inactive" else _bare(model)
        return None

    def _fetch_context_size(self, model: str) -> int | None:
        data = self._get_json("/api/extra/true_max_context_length", timeout=1.5)
        if isinstance(data, dict):
            value = data.get("value")
            if isinstance(value, int) and value > 0:
                return value
        return None


def _bare(name: str) -> str:
    """KoboldCpp reports its model as "koboldcpp/<name>" — its own brand
    on the id. The picker shows bare names under provider captions, so
    the engine's prefix goes; chat is unaffected, the engine ignores the
    request's model field."""
    return name.removeprefix("koboldcpp/")
