"""The OpenAI wire protocol as pure functions, and the types that cross
it: what a request body is, how a thinking level is spelled on each
knob an engine reads, how an image rides on a message, and what a
streamed frame carries. Nothing here touches the network or knows an
engine — the clients compose these, and every one of them speaks this.
"""

import base64
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

# ---------- the vocabulary ----------

# Thinking effort as otaku spells it, "off" and then weakest to
# strongest. "off" asks the engine not to think; no level at all (None)
# sends nothing and leaves the engine its default.
THINKING_LEVELS: tuple[str, ...] = ("off", "low", "medium", "high", "xhigh", "max")
ALL_THINKING_LEVELS: frozenset[str] = frozenset(THINKING_LEVELS)
ThinkingLevel = Literal["off", "low", "medium", "high", "xhigh", "max"]

# The request fields a level may go out on; each client declares which
# its engine reads (`Client.thinking_knobs`).
THINKING_EFFORT_KNOB = "reasoning_effort"  # OpenAI's: the level by name, "none" for off
THINKING_FLAG_KNOB = "enable_thinking"  # the chat template's flag, sent in chat_template_kwargs
_THINKING_EFFORT_WORD = {"off": "none"}  # where the wire's word differs from ours
_THINKING_LEVEL_WORD = {"none": "off", **{level: level for level in THINKING_LEVELS}}

# ---------- what crosses the wire ----------


class WireMessage(Protocol):
    """What a chat request needs of a message: a role and its wire text.
    `store.schema.Message` satisfies it structurally."""

    @property
    def role(self) -> str: ...
    @property
    def body(self) -> str: ...


@dataclass(frozen=True)
class Image:
    """A picture for a vision model, as bytes and their media type
    ("image/png", "image/jpeg"). Always sent inline as base64: the
    local engines take nothing else, and one form serves all."""

    data: bytes
    media_type: str


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Thinking:
    text: str


@dataclass(frozen=True)
class Stats:
    """What one stream came to, as the wire's usage report and the
    clock say: the tokens, the context they sat in, and the two spans
    that split a turn — the wait for the first token, thinking or text
    (the prefill), and the rest (the generation, whose tok/s is
    completion_tokens over duration minus that wait)."""

    prompt_tokens: int | None
    # The tokens generated, thinking included where the engine counts it
    # so — the wire's own word for them.
    completion_tokens: int | None
    # Of prompt_tokens, served from the provider's cache — None where the
    # provider reports nothing.
    cached_tokens: int | None
    # The loaded context size, when the engine exposes it.
    context_max: int | None
    duration_seconds: float  # request sent → stream ended
    # Request sent → first token, thinking or text; None when none came.
    first_token_seconds: float | None


Chunk = Text | Thinking | Stats

# ---------- requests ----------


def chat_completion_body(
    model: str,
    messages: Sequence[WireMessage],
    params: dict[str, object],
    *,
    images: Sequence[Image] = (),
    cache_ttl: str | None = None,
) -> dict[str, object]:
    """A streaming chat request. `cache_ttl` marks prompt-cache
    breakpoints ("5m" or "1h"); None leaves the messages plain strings.
    `images` ride on the LAST message, the one asking about them."""
    wire: list[dict[str, object]] = [{"role": m.role, "content": m.body} for m in messages]
    if cache_ttl and wire:
        _mark_cache(wire, messages, cache_ttl)
    if images and wire:
        wire[-1] = _with_images(wire[-1], images)
    return {
        "model": model,
        "messages": wire,
        "stream": True,
        "stream_options": {"include_usage": True},
        **params,
    }


def text_completion_body(model: str, prompt: str, params: dict[str, object]) -> dict[str, object]:
    """A streaming text-completion request: the prompt continued as it
    stands, no template applied by anyone."""
    return {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "stream_options": {"include_usage": True},
        **params,
    }


def thinking_fields(level: str | None, knobs: frozenset[str]) -> dict[str, object]:
    """The request fields that carry `level`, one per knob the engine
    reads: the effort by OpenAI's name ("none" for off), the template's
    flag as false for off and true for any level. No level, or no knob,
    is nothing — the engine keeps its default. Raises ValueError for a
    word outside the vocabulary: a misspelling must not travel silently."""
    if level is None:
        return {}
    if level not in ALL_THINKING_LEVELS:
        raise ValueError(f"unknown thinking level {level!r}")
    fields: dict[str, object] = {}
    if THINKING_EFFORT_KNOB in knobs:
        fields[THINKING_EFFORT_KNOB] = _THINKING_EFFORT_WORD.get(level, level)
    if THINKING_FLAG_KNOB in knobs:
        fields["chat_template_kwargs"] = {THINKING_FLAG_KNOB: level != "off"}
    return fields


def effort_levels(words: Iterable[object]) -> frozenset[str]:
    """A catalog's effort words as our levels: OpenAI's "none" is "off",
    the rest are shared, and a word we do not spell ("minimal") is
    dropped."""
    return frozenset(
        _THINKING_LEVEL_WORD[word]
        for word in words
        if isinstance(word, str) and word in _THINKING_LEVEL_WORD
    )


def _mark_cache(wire: list[dict[str, object]], messages: Sequence[WireMessage], ttl: str) -> None:
    """Prompt-cache breakpoints, in place: the system row and the final
    row become content PARTS carrying `cache_control`; everything
    between stays a plain string. Two breakpoints are enough — the
    provider's lookup scans block boundaries backwards from a marker for
    the longest cached prefix, so one rolling trailing mark per request
    finds last turn's entry on its own. "5m" is the marking's own
    default and is not spelled; "1h" is."""
    marker: dict[str, object] = {"type": "ephemeral"}
    if ttl == "1h":
        marker["ttl"] = "1h"

    def marked(message: WireMessage) -> dict[str, object]:
        part = {"type": "text", "text": message.body, "cache_control": dict(marker)}
        return {"role": message.role, "content": [part]}

    if messages[0].role == "system":
        wire[0] = marked(messages[0])
    if len(messages) > 1 or messages[0].role != "system":
        wire[-1] = marked(messages[-1])


def _with_images(message: dict[str, object], images: Sequence[Image]) -> dict[str, object]:
    """`message` with the images appended as content parts, the text
    becoming a part of its own where it was a plain string."""
    content = message["content"]
    parts = list(content) if isinstance(content, list) else [{"type": "text", "text": content}]
    for image in images:
        encoded = base64.b64encode(image.data).decode("ascii")
        url = f"data:{image.media_type};base64,{encoded}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {**message, "content": parts}


# ---------- answers ----------


def read_usage(event: dict[str, Any]) -> tuple[int | None, int | None, int | None] | None:
    """The usage report on a frame — prompt, completion and cached token
    counts — or None when the frame carries none. Cached is OpenAI's
    spelling first (OpenRouter and llama.cpp use it), Anthropic's own as
    the fallback."""
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens")
    else:
        cached = usage.get("cache_read_input_tokens")
    return (
        positive_int(usage.get("prompt_tokens")),
        positive_int(usage.get("completion_tokens")),
        cached if isinstance(cached, int) else None,
    )


def chat_delta(event: dict[str, Any]) -> tuple[str, str]:
    """A chat frame's (thinking, text) deltas, "" where absent. Thinking
    arrives as `reasoning_content` (llama.cpp, omlx, KoboldCpp) or
    `reasoning` (Ollama, OpenRouter, LM Studio)."""
    delta = _choice(event).get("delta")
    if not isinstance(delta, dict):
        return "", ""
    thinking = delta.get("reasoning_content") or delta.get("reasoning") or ""
    return str(thinking), str(delta.get("content") or "")


def completion_delta(event: dict[str, Any]) -> tuple[str, str]:
    """A text-completion frame's deltas in the chat frame's shape: no
    thinking, the continuation as text."""
    return "", str(_choice(event).get("text") or "")


def trouble(event: dict[str, Any]) -> str:
    """The provider's own sentence when a frame is a refusal or an
    in-stream error instead of content, "" otherwise. Swallowed, such a
    frame would end the stream as an empty "ok" reply."""
    failure = event.get("error")
    if isinstance(failure, dict) and failure.get("message"):
        return str(failure["message"])
    delta = _choice(event).get("delta")
    if isinstance(delta, dict) and delta.get("refusal"):
        return str(delta["refusal"])
    return ""


def positive_int(value: object) -> int | None:
    """`value` when it is a positive int — what every count and window
    read off the wire is checked with — else None."""
    return value if isinstance(value, int) and value > 0 else None


def _choice(event: dict[str, Any]) -> dict[str, Any]:
    choices = event.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return {}
