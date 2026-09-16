"""The generic provider: any server behind the OpenAI protocol, by url
and key, over the protocol alone. The base listing reads /models, with
the model's own max context where an entry carries `context_length` or
llama.cpp's `meta`, and the served window where it carries the vLLM
extension `max_model_len` or that `meta` — and nothing an engine's API
would add: no state, no sizes, every capability unknown, which a reader
treats as no. Permissive, never
restrictive: what the server reads cannot be known, so everything the
app can send goes out — every parameter, the penalty under both its
spellings, a thinking level on every knob an engine could read — in
the hope it is taken, and the server drops what it does not read; one
that refuses an unknown field names it, and the take goes out again
without the knobs. The url may name a local engine as well as a
catalog, so `locality` stays UNKNOWN. Configured entirely by hand, so
no first-run section.
"""

from typing import ClassVar

from otaku.providers.openai import reasoning
from otaku.providers.openai.client import OpenAIClient
from otaku.providers.openai.completion import PROTOCOL_PARAMS, SAMPLER_PARAMS, OpenAICompletion


class GenericCompletion(OpenAICompletion):
    supported_params = PROTOCOL_PARAMS | SAMPLER_PARAMS  # permissive: see the module
    # The rung by name and as the template's variable, the template's
    # flag, and both budgets — the one off that holds on a template that
    # reads neither, behind llama.cpp's or omlx's url. Not OpenRouter's
    # object: it carries only on and a number, and a model the provider
    # cannot describe is offered neither.
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {
            reasoning.BUDGET_KNOB,
            reasoning.BUDGET_TOKENS_KNOB,
            reasoning.EFFORT_KNOB,
            reasoning.EFFORT_TEMPLATE_KNOB,
            reasoning.SWITCH_TEMPLATE_KNOB,
        }
    )
    # On the text wire no template stands: the protocol's own field,
    # which KoboldCpp reads there, and the budgets, which are sampling.
    text_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {reasoning.BUDGET_KNOB, reasoning.BUDGET_TOKENS_KNOB, reasoning.EFFORT_KNOB}
    )

    def _convert_params(self, params: dict[str, object]) -> dict[str, object]:
        # `repeat_penalty` beside `repetition_penalty`: llama.cpp and LM
        # Studio behind this url read only the former.
        if "repetition_penalty" not in params:
            return params
        return {**params, "repeat_penalty": params["repetition_penalty"]}


class GenericClient(OpenAIClient):
    id = "generic"
    label = "Generic OpenAI provider"
    env_key = "GENERIC_API_KEY"
    completion_class = GenericCompletion
