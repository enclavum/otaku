"""How a request is written: what a chat request needs of a message,
how an image rides on one, and the two streaming bodies. Pure — nothing
here touches the network or knows an engine.
"""

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


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
        _mark_cache(wire, cache_ttl)
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


def _mark_cache(wire: list[dict[str, object]], ttl: str) -> None:
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

    def marked(row: dict[str, object]) -> dict[str, object]:
        if not row["content"]:
            return row  # an empty text part is refused by the catalogs
        part = {"type": "text", "text": row["content"], "cache_control": dict(marker)}
        return {**row, "content": [part]}

    if wire[0]["role"] == "system":
        wire[0] = marked(wire[0])
    if len(wire) > 1 or wire[0]["role"] != "system":
        wire[-1] = marked(wire[-1])


def _with_images(message: dict[str, object], images: Sequence[Image]) -> dict[str, object]:
    """`message` with the images appended as content parts, the text
    becoming a part of its own where it was a plain string."""
    content = message["content"]
    if isinstance(content, list):
        parts = list(content)
    else:
        # An empty text part is refused by the catalogs; no words, no part.
        parts = [{"type": "text", "text": content}] if content else []
    for image in images:
        encoded = base64.b64encode(image.data).decode("ascii")
        url = f"data:{image.media_type};base64,{encoded}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {**message, "content": parts}
