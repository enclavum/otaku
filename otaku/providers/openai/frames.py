"""How an answer's frame is read: the usage report, the deltas of each
wire, and a frame that is trouble instead of content. Pure — nothing
here touches the network or knows an engine.
"""

from typing import Any

from otaku.providers.http import explanation, positive_int

Usage = tuple[int | None, int | None, int | None]  # prompt, completion and cached tokens


def chat_delta(event: dict[str, Any]) -> tuple[str, str]:
    """A chat frame's (reasoning, text) deltas, "" where absent. It
    arrives as `reasoning_content` (llama.cpp, omlx, KoboldCpp),
    `reasoning` (Ollama, LM Studio), or OpenRouter's `reasoning_details`
    — typed parts, of which the text and a summary are readable and an
    encrypted one is not."""
    delta = _choice(event).get("delta")
    if not isinstance(delta, dict):
        return "", ""
    reasoning = (
        delta.get("reasoning_content")
        or delta.get("reasoning")
        or _details(delta.get("reasoning_details"))
    )
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


def finish(event: dict[str, Any]) -> str | None:
    """Why the model stopped, where a frame says: the choice's
    `finish_reason` as the wire spells it ("stop", "length",
    "content_filter", "error"); None on a frame that carries none."""
    reason = _choice(event).get("finish_reason")
    return reason if isinstance(reason, str) and reason else None


def trouble(event: dict[str, Any]) -> str:
    """The sentence a frame is trouble by, "" otherwise. The model's own
    refusal — a `refusal` delta, or a stop by `content_filter` — is
    "The model declined: …"; a server's in-stream error — an `error`
    object or string in place of content, or a stop by `finish_reason`
    "error" (KoboldCpp's failed generation, which then ends cleanly) —
    is "The reply broke off: …", in the server's words where it gave
    any. Swallowed, such a frame would end the stream as an "ok" reply."""
    # The frame's error object, explained whole (`http.explanation`):
    # OpenRouter's mid-stream errors carry the same metadata as its
    # refusals.
    failure = event.get("error")
    if isinstance(failure, dict | str) and (said := explanation(event)):
        return f"The reply broke off: {said}"
    choice = _choice(event)
    delta = choice.get("delta")
    if isinstance(delta, dict) and delta.get("refusal"):
        return f"The model declined: {delta['refusal']}"
    if choice.get("finish_reason") == "error":
        return "The reply broke off: the generation stopped with an error"
    if choice.get("finish_reason") == "content_filter":
        return "The model declined: the reply was filtered"
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


def _details(parts: object) -> str:
    """OpenRouter's reasoning parts as one text: each part's `text` or
    `summary`, in order; an encrypted part has neither and adds nothing."""
    if not isinstance(parts, list):
        return ""
    return "".join(
        str(part.get("text") or part.get("summary") or "")
        for part in parts
        if isinstance(part, dict)
    )


def _choice(event: dict[str, Any]) -> dict[str, Any]:
    choices = event.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return {}
