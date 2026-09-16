"""The thinking level: otaku's vocabulary for it, and how a level is
spelled on each request field an engine reads.

A level has one of three shapes, and a model takes one shape of word
(`ModelCapabilities`): a rung of the ladder (`EFFORT_LEVELS`), a position of
the switch (`SWITCH_LEVELS`), or a budget in tokens — digits, 0 = off.
"""

from collections.abc import Iterable

# The ladder, the wire's own words: "none" and then weakest to
# strongest. "none" asks the engine not to reason; no level at all
# (None) sends nothing and leaves the engine its default.
EFFORT_LEVELS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
ALL_EFFORT_LEVELS: frozenset[str] = frozenset(EFFORT_LEVELS)  # a model that honours every rung
# The switch, for a model whose thinking has two positions and no
# grades: "off" is spelled as "none" on every knob, "on" as the flag
# alone — nothing on the knobs that name a rung.
SWITCH_LEVELS: tuple[str, ...] = ("off", "on")

# The request fields a level may go out on, as the path each takes in
# the body; each engine's completion half declares which its engine
# reads. By name, which is by shape.
#
# A budget in tokens, enforced by the engine's own sampler whatever the
# template reads: the one off switch that holds on every thinking
# model, and the budget a level names. Carried as 0 for off, as the
# number for a budget, and not at all for a rung or on. omlx's
# spelling, OpenRouter's (in its reasoning object, spent as the model
# takes it) and llama.cpp's (KoboldCpp takes it as an alias).
BUDGET_KNOB = "thinking_budget"
BUDGET_MAX_TOKENS_KNOB = "reasoning.max_tokens"
BUDGET_TOKENS_KNOB = "thinking_budget_tokens"
# The rung by name: OpenAI's field, and the same word as a template
# variable — what the templates that grade their reasoning (gpt-oss's)
# read, and what an engine that forwards chat_template_kwargs verbatim
# (llama.cpp, omlx) reads at all. For none too: the flag alone is not
# the off switch — a template that reads no flag turns its reasoning
# off on the word (Cohere's), and one that reads neither is switched
# off by the budget.
EFFORT_KNOB = "reasoning_effort"
EFFORT_TEMPLATE_KNOB = "chat_template_kwargs.reasoning_effort"
# The switch's position: the template's flag, false for off.
SWITCH_TEMPLATE_KNOB = "chat_template_kwargs.enable_thinking"


def fields(level: str | None, knobs: frozenset[str]) -> dict[str, object]:
    """The request fields that carry `level`, one per knob the engine
    reads, each placed at its path. No level, or no knob, is nothing:
    the engine keeps its default. Raises ValueError for a word outside
    the vocabulary, so a misspelling cannot travel silently."""
    if level is None:
        return {}
    budget = budget_of(level)
    if budget is None and level not in EFFORT_LEVELS and level not in SWITCH_LEVELS:
        raise ValueError(f"unknown thinking level {level!r}")
    off = level in ("none", "off") or budget == 0
    # The ladder's word where the level is one, or off: a switch's on
    # and a budget name no rung.
    word = "none" if off else level if level in EFFORT_LEVELS else None
    # What each knob carries for this level; None, and it carries nothing.
    values: dict[str, object] = {
        BUDGET_KNOB: 0 if off else budget,
        BUDGET_MAX_TOKENS_KNOB: budget or None,  # off goes as none, never as a budget of 0
        BUDGET_TOKENS_KNOB: 0 if off else budget,
        EFFORT_KNOB: word,
        EFFORT_TEMPLATE_KNOB: word,
        SWITCH_TEMPLATE_KNOB: not off,
    }
    out: dict[str, object] = {}
    for knob, value in values.items():
        if knob not in knobs or value is None:
            continue
        head, dot, rest = knob.partition(".")  # the path, one dot deep at most
        if not dot:
            out[head] = value
        else:
            nested = out.setdefault(head, {})
            assert isinstance(nested, dict)
            nested[rest] = value
    return out


def budget_of(level: str) -> int | None:
    """The budget a level names — digits, 0 = off — or None for a word."""
    return int(level) if level.isdecimal() else None


def is_level(word: str) -> bool:
    """Whether `word` is spelled in the vocabulary: a rung, a switch
    position, or a budget."""
    return word in EFFORT_LEVELS or word in SWITCH_LEVELS or budget_of(word) is not None


def from_wire(words: Iterable[object]) -> frozenset[str]:
    """A catalog's effort words, a word we do not spell dropped."""
    return frozenset(word for word in words if isinstance(word, str) and word in EFFORT_LEVELS)
