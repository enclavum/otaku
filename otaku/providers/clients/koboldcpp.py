"""KoboldCpp: one model per process, chosen at launch — no load/unload.
Chat rides the OpenAI protocol at /v1; the native surface adds admin
mode's active model, the true context window, the feature flags of
`/api/extra/version` (vision among them), and a token count that
renders a chat request the way a turn would. A thinking level goes out
on both knobs: `reasoning_effort` becomes a thinking BUDGET on every
launch — off is zero, low and medium a share of the output cap, high
and above unlimited — and the template's flag is read when the server
runs with `--jinja`.
"""

from collections.abc import Sequence
from typing import ClassVar

from otaku.providers import http, wire
from otaku.providers.client import (
    ASK_TIMEOUT,
    PROBE_TIMEOUT,
    Capabilities,
    Client,
    Locality,
    ModelInfo,
)
from otaku.providers.clients import launched_port
from otaku.providers.wire import THINKING_EFFORT_KNOB, THINKING_FLAG_KNOB, WireMessage, positive_int
from otaku.settings.providers import ProviderConfig

# The levels the budget tells apart; high and above are unlimited, which
# is the model's own default and so not a level of its own.
_BUDGET_LEVELS: frozenset[str] = frozenset({"off", "low", "medium"})


class KoboldCppClient(Client):
    kind = "koboldcpp"
    label = "KoboldCpp"
    locality = Locality.LOCAL
    env_key = "KOBOLDCPP_API_KEY"
    thinking_knobs: ClassVar[frozenset[str]] = frozenset({THINKING_EFFORT_KNOB, THINKING_FLAG_KNOB})
    # The budget is applied on every path, the raw one included.
    text_thinking_knobs: ClassVar[frozenset[str]] = frozenset({THINKING_EFFORT_KNOB})
    counts_tokens = True

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # Configured by launch flags — nothing on disk to detect a port
        # from, so a running server's own flag is read, else the standard
        # default.
        port = launched_port("koboldcpp") or 5001
        return ProviderConfig(name=cls.kind, url=f"http://localhost:{port}/v1")

    def models(self, timeout: float = ASK_TIMEOUT) -> list[ModelInfo]:
        """The single-model listing, the engine's own name prefix
        stripped; admin mode's active state refines `loaded` when that
        surface answers. The context size is the server's: one probe
        under the first name stamps every row."""
        active = self._active_model(timeout=PROBE_TIMEOUT)
        names = sorted(_bare(row.name) for row in super().models(timeout))
        size = self.get_context_size(names[0]) if names else None
        # The feature flags are the server's too: vision means a
        # projector was loaded beside the model.
        version = http.get_json(
            f"{self.config.base_url}/api/extra/version",
            name=self.config.name,
            headers=self._headers,
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        vision = bool(version.get("vision", True)) if isinstance(version, dict) else True
        capabilities = Capabilities(vision=vision, thinking=_BUDGET_LEVELS)
        return [
            ModelInfo(
                name=name,
                capabilities=capabilities,
                context=size,
                loaded=name == active if active is not None else True,
            )
            for name in names
        ]

    def _context_size(self, model: str) -> int | None:
        data = http.get_json(
            f"{self.config.base_url}/api/extra/true_max_context_length",
            name=self.config.name,
            headers=self._headers,
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        return positive_int(data.get("value")) if isinstance(data, dict) else None

    def count_chat_tokens(
        self, model: str, messages: Sequence[WireMessage], timeout: float = ASK_TIMEOUT
    ) -> int | None:
        # Given messages, the count renders them through the same
        # transform a chat turn gets, jinja template included.
        body = {"messages": wire.chat_completion_body(model, messages, {})["messages"]}
        return self._count(body, timeout)

    def count_text_tokens(
        self, model: str, prompt: str, timeout: float = ASK_TIMEOUT
    ) -> int | None:
        return self._count({"prompt": prompt}, timeout)

    def _count(self, body: dict[str, object], timeout: float) -> int | None:
        data = http.post_json(
            f"{self.config.base_url}/api/extra/tokencount",
            body,
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
            quiet=True,
        )
        count = data.get("value") if isinstance(data, dict) else None
        return count if isinstance(count, int) else None

    def _active_model(self, timeout: float) -> str | None:
        """The model admin mode reports as active; "" between swaps
        ("inactive"), None when the endpoint does not answer."""
        data = http.get_json(
            f"{self.config.base_url}/api/v1/model",
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
            quiet=True,
        )
        if isinstance(data, dict):
            model = data.get("result")
            if isinstance(model, str):
                return "" if model == "inactive" else _bare(model)
        return None


def _bare(name: str) -> str:
    """KoboldCpp reports its model as "koboldcpp/<name>" — its own brand
    on the id. The picker shows bare names under provider captions, so
    the engine's prefix goes; chat is unaffected, the engine ignores the
    request's model field."""
    return name.removeprefix("koboldcpp/")
