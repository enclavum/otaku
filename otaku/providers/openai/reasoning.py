"""The reasoning effort: otaku's vocabulary for it, and how an effort is
spelled on each request field an engine reads.
"""

from collections.abc import Iterable

# The wire's own words: "none" and then weakest to strongest. "none"
# asks the engine not to reason; no effort at all (None) sends nothing
# and leaves the engine its default.
EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
ALL_EFFORTS: frozenset[str] = frozenset(EFFORTS)  # the value for a model that honours every effort

# The request fields an effort may go out on, as the path each takes in
# the body; each engine's completion half declares which its engine
# reads.
EFFORT_KNOB = "reasoning_effort"  # OpenAI's: the effort by name
FLAG_KNOB = "chat_template_kwargs.enable_thinking"  # the template's flag: false for none
# The effort as a template variable, beside the flag: what the templates
# that grade their reasoning (gpt-oss's) read, and what an engine that
# forwards chat_template_kwargs verbatim (llama.cpp, omlx) reads at all.
# Never for none, which is the flag's to say.
TEMPLATE_EFFORT_KNOB = "chat_template_kwargs.reasoning_effort"


def fields(effort: str | None, knobs: frozenset[str]) -> dict[str, object]:
    """The request fields that carry `effort`, one per knob the engine
    reads, each placed at its path. No effort, or no knob, is nothing:
    the engine keeps its default. Raises ValueError for a word outside
    the vocabulary, so a misspelling cannot travel silently."""
    if effort is None:
        return {}
    if effort not in EFFORTS:
        raise ValueError(f"unknown reasoning effort {effort!r}")
    values: dict[str, object] = {
        EFFORT_KNOB: effort,
        FLAG_KNOB: effort != "none",
        TEMPLATE_EFFORT_KNOB: effort,
    }
    out: dict[str, object] = {}
    for knob in (EFFORT_KNOB, FLAG_KNOB, TEMPLATE_EFFORT_KNOB):
        if knob not in knobs or (knob == TEMPLATE_EFFORT_KNOB and effort == "none"):
            continue
        head, dot, rest = knob.partition(".")  # the path, one dot deep at most
        if not dot:
            out[head] = values[knob]
        else:
            nested = out.setdefault(head, {})
            assert isinstance(nested, dict)
            nested[rest] = values[knob]
    return out


def from_wire(words: Iterable[object]) -> frozenset[str]:
    """A catalog's effort words, a word we do not spell dropped."""
    return frozenset(word for word in words if isinstance(word, str) and word in EFFORTS)
