"""The OpenAI-compatible client — and the base of every backend.

`OpenAIClient` speaks the OpenAI wire protocol: `/models` to list and
streaming `/chat/completions` to generate. That is the whole protocol
surface. The passive introspection on this class — loaded-state, sizes,
the context window — is NOT OpenAI: those are questions any caller may ask
any backend, answered honestly as "unknown" here and overridden by
backends whose native APIs can do better. A provider that is just an
OpenAI endpoint — local or remote — is served by this class as is.

`ManagedClient` extends it with the ACTIONS: a backend that can load and
unload models on demand. UI checks `isinstance(client, ManagedClient)` to
know whether to offer them.

`chat_stream` yields typed chunks: `Thinking` deltas, `Text` deltas, and a
final `Stats`. Bursty output is re-timed into an even flow when smoothing
is on (see `providers.streaming`); calls nobody watches pass smooth=False
and skip it.
"""

import contextlib
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import httpx

from otaku.logs.requests import RequestLog
from otaku.providers import streaming
from otaku.settings.config import Provider


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Thinking:
    text: str


@dataclass(frozen=True)
class Stats:
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_seconds: float
    # The loaded context window, when the backend exposes it.
    context_max: int | None = None
    # Decode-only span: first emitted token → end of stream, excluding the
    # prefill — the honest tok/s denominator.
    generation_seconds: float | None = None


Chunk = Text | Thinking | Stats


class WireMessage(Protocol):
    """What a chat request needs of a message: a role and its wire text.
    `store.schema.Message` satisfies it structurally."""

    @property
    def role(self) -> str: ...
    @property
    def body(self) -> str: ...


class OpenAIClient:
    kind: ClassVar[str] = "openai"

    def __init__(
        self,
        provider: Provider,
        *,
        request_log: RequestLog | None = None,
        smooth: bool = False,
    ) -> None:
        self.provider = provider
        self._request_log = request_log
        self._smooth = smooth
        self._context_cache: dict[str, int] = {}

    # ---------- the OpenAI protocol ----------

    def list_models(self, timeout: float = 10.0) -> list[str]:
        response = httpx.get(
            f"{self.provider.url}/models", headers=self.provider.headers, timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        return sorted(str(m["id"]) for m in data.get("data", []))

    def chat_stream(
        self,
        model: str,
        messages: Sequence[WireMessage],
        params: dict[str, object],
        *,
        think: str | None = None,
        timeout: float = 600.0,
        purpose: str = "chat",
        smooth: bool = True,
    ) -> Iterator[Chunk]:
        """Stream one completion. `smooth=False` for calls nobody watches —
        an accumulated string gains nothing from pacing, and the held lag
        would only delay their cancellation."""
        body: dict[str, object] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.body} for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            **params,
        }
        self._apply_thinking(body, think)
        if self._request_log is not None:
            self._request_log.record(self.provider.name, purpose, body)
        stream = self._stream(model, body, timeout)
        if smooth and self._smooth:
            return streaming.smooth(stream)
        return stream

    def _stream(self, model: str, body: dict[str, object], timeout: float) -> Iterator[Chunk]:
        start = time.monotonic()
        first_token_at: float | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None

        with httpx.stream(
            "POST",
            f"{self.provider.url}/chat/completions",
            json=body,
            headers=self.provider.headers,
            timeout=timeout,
        ) as response:
            if response.status_code >= 400:
                # Drain now, while the stream is open — the error body (the
                # server's explanation) must stay readable after close.
                with contextlib.suppress(httpx.HTTPError):
                    response.read()
            response.raise_for_status()
            for raw in response.iter_lines():
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                usage = event.get("usage")
                if isinstance(usage, dict):
                    prompt_tokens = usage.get("prompt_tokens")
                    completion_tokens = usage.get("completion_tokens")
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                thinking = delta.get("reasoning_content") or delta.get("reasoning")
                if thinking:
                    if first_token_at is None:
                        first_token_at = time.monotonic()
                    yield Thinking(text=str(thinking))
                content = delta.get("content")
                if content:
                    if first_token_at is None:
                        first_token_at = time.monotonic()
                    yield Text(text=str(content))

        end = time.monotonic()
        yield Stats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=end - start,
            context_max=self.get_context_size(model),
            generation_seconds=(end - first_token_at) if first_token_at is not None else None,
        )

    # ---------- passive introspection (native APIs; defaults = unknown) ----------

    def get_loaded_models(self, timeout: float = 1.5) -> set[str]:
        return set()

    def get_model_sizes(self, timeout: float = 5.0) -> dict[str, int]:
        return {}

    def get_context_size(self, model: str) -> int | None:
        """The loaded context window for `model`, or None when the backend
        does not expose it. Only a real answer is cached — None retries,
        because the usual cause is asking before the model loads."""
        if model in self._context_cache:
            return self._context_cache[model]
        result = self._fetch_context_size(model)
        if result is not None:
            self._context_cache[model] = result
        return result

    def _apply_thinking(self, body: dict[str, object], think: str | None) -> None:
        """Translate the think setting into the request. Base = OpenAI-style
        `reasoning_effort`: a level enables it, "none" actively disables it,
        None sends nothing and leaves the backend's default."""
        if think and self.provider.supports_thinking:
            body["reasoning_effort"] = think

    def _fetch_context_size(self, model: str) -> int | None:
        return None

    # ---------- helpers for the backends ----------

    def _get_json(self, path: str, *, timeout: float) -> Any | None:
        """GET `<base url>{path}` → parsed JSON; None on any error or
        non-200. For best-effort native-API reads only."""
        try:
            response = httpx.get(
                f"{self.provider.base_url}{path}", headers=self.provider.headers, timeout=timeout
            )
            if response.status_code == 200:
                return response.json()
        except (httpx.HTTPError, OSError, ValueError):
            pass
        return None


class ManagedClient(OpenAIClient, ABC):
    """A backend that can load and unload models on demand — the actions on
    top of the passive base. UI offers load/unload exactly when a client is
    one of these."""

    @abstractmethod
    def load_model(self, model: str) -> None: ...

    @abstractmethod
    def unload_model(self, model: str) -> None: ...
