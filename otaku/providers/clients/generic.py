"""The generic provider: any server behind the OpenAI protocol, by url
and key, over the protocol alone. Models come from /models — a window
read when the listing carries `context_length`, the extension the
catalogs share — and turns stream from /chat/completions. Nothing an
engine's own API would add: no load state, no sizes, no window
otherwise, and every capability unknown, which reads as allowed. The
url may name a local engine as well as a catalog, so a thinking level
goes out on both knobs and `locality` stays UNKNOWN. The one provider
a reader configures entirely by hand, so it has no first-run section.
"""

from typing import ClassVar

from otaku.providers.client import ASK_TIMEOUT, Client, ModelInfo
from otaku.providers.wire import THINKING_EFFORT_KNOB, THINKING_FLAG_KNOB


class OpenAIClient(Client):
    kind = "generic"
    label = "Generic OpenAI provider"
    env_key = "OPENAI_API_KEY"
    thinking_knobs: ClassVar[frozenset[str]] = frozenset({THINKING_EFFORT_KNOB, THINKING_FLAG_KNOB})
    # On the text wire only the protocol's own field can travel.
    text_thinking_knobs: ClassVar[frozenset[str]] = frozenset({THINKING_EFFORT_KNOB})

    def models(self, timeout: float = ASK_TIMEOUT) -> list[ModelInfo]:
        return self._full_models(timeout)
