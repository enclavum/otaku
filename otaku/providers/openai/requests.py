"""How a request is written: what a chat request needs of a message,
how an image rides on one, and the two streaming bodies. Pure — nothing
here touches the network or knows an engine.
"""

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class WireImage(Protocol):
    """What a chat request needs of a picture on a message: its bytes
    and their media type. `Image` is the concrete one; the assembler's
    own picture satisfies it structurally, the way its wire turn
    satisfies `WireMessage`."""

    @property
    def data(self) -> bytes: ...
    @property
    def media_type(self) -> str: ...


class WireMessage(Protocol):
    """What a chat request needs of a message: a role, its wire text,
    and the pictures riding on it (none, for most). `context`'s
    `WireTurn` satisfies it structurally."""

    @property
    def role(self) -> str: ...
    @property
    def body(self) -> str: ...
    @property
    def images(self) -> Sequence[WireImage]: ...


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
    cache_ttl: str | None = None,
) -> dict[str, object]:
    """A streaming chat request. Each message's pictures ride on it as
    content parts after its text. `cache_ttl` marks prompt-cache
    breakpoints ("5m" or "1h") — on a row that carries pictures, on its
    last part, so the pictures sit inside the cached prefix; None leaves
    the messages plain strings."""
    wire: list[dict[str, object]] = [{"role": m.role, "content": m.body} for m in messages]
    for at, message in enumerate(messages):
        if message.images:
            wire[at] = _with_images(wire[at], message.images)
    if cache_ttl and wire:
        _mark_cache(wire, cache_ttl)
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
        content = row["content"]
        if isinstance(content, list):
            # Already parts — a row with pictures: the mark goes on the
            # last one, so the whole row is inside the cached prefix.
            if not content:
                return row
            parts = list(content)
            parts[-1] = {**parts[-1], "cache_control": dict(marker)}
            return {**row, "content": parts}
        if not content:
            return row  # an empty text part is refused by the catalogs
        part = {"type": "text", "text": content, "cache_control": dict(marker)}
        return {**row, "content": [part]}

    if wire[0]["role"] == "system":
        wire[0] = marked(wire[0])
    if len(wire) > 1 or wire[0]["role"] != "system":
        wire[-1] = marked(wire[-1])


def _with_images(message: dict[str, object], images: Sequence[WireImage]) -> dict[str, object]:
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
