"""The generic provider: any server behind the OpenAI protocol, by url
and key, over the protocol alone. The base listing reads /models, with
the model's own ceiling where an entry carries `context_length`, and
nothing an engine's API would add — no state, no sizes, every
capability unknown, which a reader treats as no. The url may name a
local engine as well as a catalog, so a reasoning effort goes out on
both chat knobs and `locality` stays UNKNOWN. Configured entirely by
hand, so no first-run section.
"""

from typing import ClassVar

from otaku.providers.openai import reasoning
from otaku.providers.openai.client import OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion


class GenericCompletion(OpenAICompletion):
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {reasoning.EFFORT_KNOB, reasoning.FLAG_KNOB}
    )
    # On the text wire only the protocol's own field can travel.
    text_reasoning_knobs: ClassVar[frozenset[str]] = frozenset({reasoning.EFFORT_KNOB})


class GenericClient(OpenAIClient):
    id = "generic"
    label = "Generic OpenAI provider"
    env_key = "GENERIC_API_KEY"
    completion_class = GenericCompletion
