"""LM Studio: the registry, load and unload, sizes, context sizes and each
model's capabilities via its /api/v1/models surface; chat rides the
OpenAI protocol at /v1. A reasoning effort goes out as
`reasoning_effort`, which the server takes since its 0.4.8 — none,
minimal, low, medium, high, xhigh; any other word is a 400, which the
take then sends again without the knob — and hands to the models that
expose reasoning, ignoring it on the others (Gemma 4 here answered the
same at none and at high). Which models those are the registry does
not say, so no effort is reported as honoured or not.
"""

from typing import Any, ClassVar

from otaku.providers.clients import read_home_json
from otaku.providers.errors import StatusError
from otaku.providers.http import PROBE_TIMEOUT, Cut, Http, positive_int
from otaku.providers.openai import reasoning
from otaku.providers.openai.client import OpenAIClient
from otaku.providers.openai.completion import PROTOCOL_PARAMS, Bounds, OpenAICompletion
from otaku.providers.openai.models import (
    Listing,
    Locality,
    ModelCapabilities,
    ModelInfo,
    ModelState,
    OpenAIModels,
)
from otaku.settings.providers import ProviderConfig


class LmStudioModels(OpenAIModels):
    @property
    def can_manage(self) -> bool:
        return True

    def _load(self, model: str, http: Http, cut: Cut) -> None:
        # Idempotent on purpose: LM Studio's /load is not — repeated
        # calls stack 'model:2', ':3', … instances. Through the order's
        # view, under its budget and its cut.
        entry = self._entry_of(model, http, quiet=False)
        if entry is not None and entry.get("loaded_instances"):
            return
        http.post(f"{self._config.base_url}/api/v1/models/load", {"model": model}, cut=cut)

    def _unload(self, model: str, http: Http, cut: Cut) -> None:
        # /unload takes an instance id: every loaded instance of the
        # model is unloaded in turn. The registry is read with its
        # errors on: a door that cannot read must say so, not do nothing.
        entry = self._entry_of(model, http, quiet=False)
        for instance in self._instances(entry) if entry is not None else []:
            instance_id = instance.get("id")
            if not isinstance(instance_id, str):
                continue
            try:
                http.post(
                    f"{self._config.base_url}/api/v1/models/unload",
                    {"instance_id": instance_id},
                    cut=cut,
                )
            except StatusError as e:
                # Gone between the read and the order — LM Studio's own
                # idle unload: the state asked for.
                if e.status != 404:
                    raise

    # ---------- the hooks ----------

    def _list(self, http: Http) -> Listing:
        """Everything from the one /api/v1/models pass: key, size,
        capabilities, the model's maximum, and a loaded instance's
        configured context size. Loud: a server that refuses the key, or
        fails, is the listing's own sentence. A 404, or an answer that
        is not the registry's — no such surface, not LM Studio — falls
        to the plain names."""
        try:
            entries = self._registry(http, quiet=False)
        except StatusError as e:
            if e.status != 404:
                raise
            entries = None
        if entries is None:
            return super()._list(http)
        models = []
        for entry in entries:
            key = entry.get("key")
            if not isinstance(key, str) or entry.get("type") == "embedding":
                continue  # an embedding model answers a chat with a 400
            max_context_loaded = self._max_context_loaded_of(entry)
            models.append(
                ModelInfo(
                    name=key,
                    size=positive_int(entry.get("size_bytes")),
                    max_context_catalogue=positive_int(entry.get("max_context_length")),
                    max_context_loaded=max_context_loaded,
                    capabilities=self._capabilities_of(entry),
                    state=ModelState.LOADED
                    if entry.get("loaded_instances")
                    else ModelState.UNLOADED,
                )
            )
        return sorted(models, key=lambda model: model.name)

    def _get(self, name: str, http: Http) -> ModelInfo | None:
        entry = self._entry_of(name, http, timeout=PROBE_TIMEOUT)
        if entry is None:
            return None
        if entry.get("loaded_instances"):
            return ModelInfo(
                name=name,
                max_context_loaded=self._max_context_loaded_of(entry),
                state=ModelState.LOADED,
            )
        return ModelInfo(name=name, state=ModelState.UNLOADED)

    # ---------- the native surface ----------

    def _entry_of(
        self, model: str, http: Http, *, timeout: float | None = None, quiet: bool = True
    ) -> dict[str, Any] | None:
        entries = self._registry(http, timeout=timeout, quiet=quiet) or []
        return next((e for e in entries if e.get("key") == model), None)

    def _registry(
        self, http: Http, *, timeout: float | None = None, quiet: bool = True
    ) -> list[dict[str, Any]] | None:
        """The registry's entries, asked through the caller's view, under
        `timeout` where the read has a cap of its own; None when the
        server did not answer, or has no such surface. Not `quiet`, a
        failure raises."""
        data = http.get(f"{self._config.base_url}/api/v1/models", timeout=timeout, quiet=quiet)
        models = data.get("models") if isinstance(data, dict) else None
        return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else None

    def _max_context_loaded_of(self, entry: dict[str, Any]) -> int | None:
        """The context size a loaded instance was configured with; None
        without an instance — the model's maximum is not what an
        instance gets."""
        for instance in self._instances(entry):
            config = instance.get("config")
            length = (
                positive_int(config.get("context_length")) if isinstance(config, dict) else None
            )
            if length:
                return length
        return None

    def _instances(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """The entry's loaded instances, each one an object; anything
        else on the list is skipped."""
        listed = entry.get("loaded_instances")
        return [i for i in listed if isinstance(i, dict)] if isinstance(listed, list) else []

    def _capabilities_of(self, entry: dict[str, Any]) -> ModelCapabilities:
        """What a registry entry says: vision from its capabilities, and
        the rungs its `reasoning.allowed_options` names — a registry
        that names none for a model, or has no reasoning object, says
        nothing of it; the raw wire is there; decoding is constrained
        server-side (`response_format`)."""
        caps = entry.get("capabilities")
        if not isinstance(caps, dict):
            return ModelCapabilities(text_completion=True, structured_output=True)
        vision = bool(caps["vision"]) if "vision" in caps else None
        spec = caps.get("reasoning")
        options = spec.get("allowed_options") if isinstance(spec, dict) else None
        levels = reasoning.from_wire(options) if isinstance(options, list) else None
        return ModelCapabilities(
            vision=vision,
            reasoning_efforts=levels,
            reasoning_switch=None if levels is None else False,
            reasoning_budget=None if levels is None else False,
            text_completion=True,
            structured_output=True,
        )


class LmStudioCompletion(OpenAICompletion):
    # Its endpoint documents `top_k` and `repeat_penalty` beyond the
    # protocol, and no `min_p`.
    supported_params = PROTOCOL_PARAMS | {"top_k", "repetition_penalty"}
    # A local engine bounds nothing the catalogs bound: any temperature,
    # any penalty.
    bounds: ClassVar[dict[str, Bounds]] = {
        "temperature": (0, None),
        "presence_penalty": (None, None),
        "frequency_penalty": (None, None),
        "repetition_penalty": (0, None),
    }
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset({reasoning.EFFORT_KNOB})

    def _convert_params(self, params: dict[str, object]) -> dict[str, object]:
        if "repetition_penalty" not in params:
            return params
        spelled = dict(params)
        spelled["repeat_penalty"] = spelled.pop("repetition_penalty")
        return spelled


class LmStudioClient(OpenAIClient):
    id = "lmstudio"
    label = "LM Studio"
    locality = Locality.LOCAL
    env_key = "LMSTUDIO_API_KEY"
    models_class = LmStudioModels
    completion_class = LmStudioCompletion

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The first-run section, its port from LM Studio's own server
        config file."""
        port = read_home_json(".lmstudio/.internal/http-server-config.json").get("port")
        url = f"http://localhost:{port if isinstance(port, int) else 1234}/v1"
        return ProviderConfig(name=cls.id, url=url)
