"""How an answer's frame is read: the usage report, the deltas of each
wire, and a frame that is trouble instead of content. Pure — nothing
here touches the network or knows an engine.
"""

from typing import Any

from otaku.providers.http import positive_int

Usage = tuple[int | None, int | None, int | None]  # prompt, completion and cached tokens


def chat_delta(event: dict[str, Any]) -> tuple[str, str]:
    """A chat frame's (reasoning, text) deltas, "" where absent. It
    arrives as `reasoning_content` (llama.cpp, omlx, KoboldCpp) or
    `reasoning` (Ollama, OpenRouter, LM Studio)."""
    delta = _choice(event).get("delta")
    if not isinstance(delta, dict):
        return "", ""
    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
    content = delta.get("content") or ""
    if isinstance(content, list):
        # Content parts on the way back: the text of each.
        content = "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return str(reasoning), str(content)


def completion_delta(event: dict[str, Any]) -> tuple[str, str]:
    """A text-completion frame's deltas in the chat frame's shape: the
    continuation as text, and the reasoning a catalog reports beside it
    (OpenRouter's `reasoning`), which the local engines never do."""
    choice = _choice(event)
    reasoning = choice.get("reasoning") or choice.get("reasoning_content") or ""
    return str(reasoning), str(choice.get("text") or "")


def trouble(event: dict[str, Any]) -> str:
    """The provider's own sentence when a frame is a refusal or an
    in-stream error instead of content, "" otherwise — or ours, when a
    frame only stops with a `finish_reason` that is trouble: "error"
    (KoboldCpp's failed generation, which then ends cleanly) or
    "content_filter". Swallowed, such a frame would end the stream as
    an "ok" reply."""
    failure = event.get("error")
    if isinstance(failure, dict) and failure.get("message"):
        return str(failure["message"])
    if isinstance(failure, str) and failure:
        return failure
    choice = _choice(event)
    delta = choice.get("delta")
    if isinstance(delta, dict) and delta.get("refusal"):
        return str(delta["refusal"])
    if choice.get("finish_reason") == "error":
        return "the generation stopped with an error"
    if choice.get("finish_reason") == "content_filter":
        return "the reply was filtered"
    return ""


def read_usage(event: dict[str, Any]) -> Usage | None:
    """The usage report on a frame, or None when the frame carries none,
    an empty object included. Cached is OpenAI's spelling first,
    Anthropic's own as the fallback."""
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if cached is None:
        cached = usage.get("cache_read_input_tokens")
    report = (
        positive_int(usage.get("prompt_tokens")),
        positive_int(usage.get("completion_tokens")),
        cached if isinstance(cached, int) else None,
    )
    return report if any(count is not None for count in report) else None


def _choice(event: dict[str, Any]) -> dict[str, Any]:
    choices = event.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return {}
