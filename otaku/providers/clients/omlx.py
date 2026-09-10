"""omlx: an MLX model server speaking the OpenAI protocol for chat, with
its own model registry and load/unload surface under /v1/models, each
model's type (a VLM takes images) in the status listing, and Anthropic's
token count at /v1/messages/count_tokens. A thinking level goes out on
both knobs: the template's flag gates thinking, and the effort is
mapped into the template where it takes one.
"""

from collections.abc import Sequence
from typing import Any, ClassVar
from urllib.parse import quote

from otaku.providers import http
from otaku.providers.client import (
    ASK_TIMEOUT,
    PROBE_TIMEOUT,
    Capabilities,
    Client,
    Locality,
    ModelInfo,
)
from otaku.providers.clients import read_home_json
from otaku.providers.wire import THINKING_EFFORT_KNOB, THINKING_FLAG_KNOB, WireMessage, positive_int
from otaku.settings.providers import ProviderConfig


class OmlxClient(Client):
    kind = "omlx"
    label = "oMLX"
    locality = Locality.LOCAL
    env_key = "OMLX_API_KEY"
    thinking_knobs: ClassVar[frozenset[str]] = frozenset({THINKING_EFFORT_KNOB, THINKING_FLAG_KNOB})
    counts_tokens = True

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The first-run section, its port and api key detected from omlx's
        own settings file."""
        settings = read_home_json(".omlx/settings.json")
        server = settings.get("server")
        port = server.get("port") if isinstance(server, dict) else None
        auth = settings.get("auth")
        key = auth.get("api_key") if isinstance(auth, dict) else None
        url = f"http://localhost:{port if isinstance(port, int) else 8000}/v1"
        return ProviderConfig(name=cls.kind, url=url, api_key=str(key) if key else "")

    @property
    def manages_models(self) -> bool:
        return True

    def load_model(self, model: str) -> None:
        self._model_action("load", model)

    def unload_model(self, model: str) -> None:
        self._model_action("unload", model)

    def _model_action(self, action: str, model: str) -> None:
        http.post_json(
            f"{self.config.base_url}/v1/models/{quote(model, safe='')}/{action}",
            {},
            name=self.config.name,
            headers=self._headers,
            timeout=None,
        )

    def models(self, timeout: float = ASK_TIMEOUT) -> list[ModelInfo]:
        """One row per model, everything from the one /v1/models/status
        pass: id, size, loaded state, and the context window. No status
        surface — not an omlx server — falls to the plain names; a dead
        server raises out of them."""
        entries = self._status(timeout)
        if not entries:
            return super().models(timeout)
        rows = []
        for entry in entries:
            model_id = entry.get("id")
            if not isinstance(model_id, str):
                continue
            # Only a VLM takes images; a type the status does not state
            # leaves the question open.
            kind = entry.get("model_type")
            vision = kind == "vlm" if isinstance(kind, str) and kind else True
            rows.append(
                ModelInfo(
                    name=model_id,
                    capabilities=Capabilities(vision=vision),
                    size=positive_int(entry.get("actual_size") or entry.get("estimated_size")),
                    context=positive_int(entry.get("max_context_window")),
                    loaded=bool(entry.get("loaded")),
                )
            )
        return sorted(rows, key=lambda row: row.name)

    def _context_size(self, model: str) -> int | None:
        entry = self._status_entry(model, timeout=PROBE_TIMEOUT)
        return positive_int(entry.get("max_context_window")) if entry is not None else None

    def count_chat_tokens(
        self, model: str, messages: Sequence[WireMessage], timeout: float = ASK_TIMEOUT
    ) -> int | None:
        # Anthropic's shape: the system text apart, the turns as messages.
        system = "\n\n".join(m.body for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": m.body} for m in messages if m.role != "system"]
        body: dict[str, Any] = {"model": model, "messages": turns}
        if system:
            body["system"] = system
        data = http.post_json(
            f"{self.config.base_url}/v1/messages/count_tokens",
            body,
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
            quiet=True,
        )
        count = data.get("input_tokens") if isinstance(data, dict) else None
        return count if isinstance(count, int) else None

    def _status_entry(self, model: str, timeout: float) -> dict[str, Any] | None:
        return next((e for e in self._status(timeout) if e.get("id") == model), None)

    def _status(self, timeout: float) -> list[dict[str, Any]]:
        data = http.get_json(
            f"{self.config.base_url}/v1/models/status",
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
            quiet=True,
        )
        models = data.get("models") if isinstance(data, dict) else None
        return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else []
