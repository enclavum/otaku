"""Pure test helpers: provider/SSE builders and a FakeClient stand-in.

Kept out of conftest.py so test modules can import them directly
(`from tests.support import ...`); conftest holds only fixtures."""

from __future__ import annotations

import json
from typing import Any

from otaku.config import Provider


def make_provider(
    name: str = "test",
    url: str = "http://localhost:9999/v1",
    *,
    api_key: str = "",
    supports_thinking: bool = False,
    keep_alive: str = "24h",
    smoothen_streaming: bool = False,
) -> Provider:
    return Provider(
        name=name,
        url=url,
        api_key=api_key,
        supports_thinking=supports_thinking,
        keep_alive=keep_alive,
        smoothen_streaming=smoothen_streaming,
    )


def sse(*chunks: dict[str, Any] | str) -> bytes:
    """Build an OpenAI-style `text/event-stream` body. Dicts become a JSON
    `data:` line; the string "[DONE]" becomes the terminator."""
    lines: list[str] = []
    for c in chunks:
        if c == "[DONE]":
            lines.append("data: [DONE]")
        else:
            lines.append("data: " + json.dumps(c))
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def content_chunk(text: str) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": text}}]}


def thinking_chunk(text: str) -> dict[str, Any]:
    return {"choices": [{"delta": {"reasoning_content": text}}]}


def usage_chunk(prompt: int, completion: int) -> dict[str, Any]:
    return {
        "choices": [],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


class FakeClient:
    """Stand-in for a ProviderClient used by CLI/registry tests. Returns canned
    data or raises the configured error, exercising cli.py orchestration
    without any HTTP."""

    def __init__(
        self,
        provider: Provider,
        *,
        kind: str = "openai",
        models: list[str] | None = None,
        loaded: set[str] | None = None,
        sizes: dict[str, int] | None = None,
        contexts: dict[str, int] | None = None,
        list_error: Exception | None = None,
        loaded_error: Exception | None = None,
        unload_error: Exception | None = None,
    ) -> None:
        self.provider = provider
        self.kind = kind
        self._models = models or []
        self._loaded = loaded or set()
        self._sizes = sizes or {}
        self._contexts = contexts or {}
        self._list_error = list_error
        self._loaded_error = loaded_error
        self._unload_error = unload_error
        self.unloaded: list[str] = []

    def list_models(self, timeout: float = 10.0) -> list[str]:
        if self._list_error is not None:
            raise self._list_error
        return sorted(self._models)

    def loaded_models(self, timeout: float = 1.5) -> set[str]:
        if self._loaded_error is not None:
            raise self._loaded_error
        return set(self._loaded)

    def model_sizes(self, timeout: float = 5.0) -> dict[str, int]:
        return dict(self._sizes)

    def context_size(self, model: str) -> int | None:
        return self._contexts.get(model)

    def unload_model(self, model: str) -> None:
        if self._unload_error is not None:
            raise self._unload_error
        self.unloaded.append(model)
        self._loaded.discard(model)

    def load_model(self, model: str) -> None:
        self._loaded.add(model)
