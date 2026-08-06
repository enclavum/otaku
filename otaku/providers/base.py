"""The OpenAI-compatible client — and the base of every backend family.

`OpenAIClient` speaks the OpenAI wire protocol: `/models` to list and
streaming `/chat/completions` to generate. That is the whole protocol
surface; a provider that is just an OpenAI endpoint is served by this
class as is. `models()` is the one listing call, returning rich
`ModelInfo` rows — the base fills only the names, and each backend
family fills what its native APIs know in one pass.

The families, one subclass each:

- `LocalSingleClient` — a single-model engine (llama.cpp, KoboldCpp):
  the server fronts one model chosen at its own launch, or none.
- `ManagedClient` — a local registry (Ollama, omlx, LM Studio) that can
  load and unload models on demand; the UI offers those actions exactly
  when a client `isinstance`-checks as one of these.
- `CloudClient` — a hosted catalog (OpenRouter, NanoGPT): models are
  listed with their context windows, never sized (no disk to weigh),
  and every row is simply available.

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
from otaku.settings.config import ProviderConfig


@dataclass(frozen=True)
class ModelInfo:
    """One model as its provider reports it — the row every listing
    returns, filled as far as the backend's native API can see."""

    name: str
    size: int | None = None  # bytes on disk; local backends only
    context: int | None = None  # the model's context window, when reported
    loaded: bool = False


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
    # Whether the backend understands a request-level thinking knob —
    # class knowledge, not configuration. The OpenAI protocol itself has
    # `reasoning_effort`, so the base says yes; engines where thinking is
    # baked into the model declare False, and /set think refuses levels.
    supports_thinking: ClassVar[bool] = True
    # Whether the backend runs on this machine. A cloud catalog says no,
    # and launch-time introspection never waits on the internet for it.
    local: ClassVar[bool] = True

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The backend's default provider section: what the provider panel
        shows before a backend is configured, and what first-run writes
        for the local engines. The plain OpenAI client has no natural
        endpoint — a generic provider is configured by hand."""
        return ProviderConfig(name=cls.kind, url="")

    def __init__(
        self,
        provider_config: ProviderConfig,
        *,
        request_log: RequestLog | None = None,
        smooth: bool = False,
    ) -> None:
        self.provider_config = provider_config
        self._request_log = request_log
        self._smooth = smooth
        self._context_cache: dict[str, int] = {}

    # ---------- the OpenAI protocol ----------

    def models(self, timeout: float = 10.0) -> list[ModelInfo]:
        """Every model this provider offers, as rich rows — the one
        listing call, never overridden. Each backend shapes its own rows
        in `_list`, the single override point; the base knows only the
        plain /models names."""
        return self._list(timeout)

    def model(self, name: str, timeout: float = 10.0) -> ModelInfo | None:
        """The listing row for one model — best effort: None when the
        provider does not offer it or cannot be reached."""
        try:
            rows = self.models(timeout)
        except Exception:
            return None
        for row in rows:
            if row.name == name:
                return row
        return None

    def _list(self, timeout: float) -> list[ModelInfo]:
        return [ModelInfo(name=name) for name in self._model_names(timeout)]

    def _model_names(self, timeout: float) -> list[str]:
        """The bare /models listing, sorted — raises when unreachable."""
        response = httpx.get(
            f"{self.provider_config.url}/models",
            headers=self.provider_config.headers,
            timeout=_timeout(timeout, connect=2.0),
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
            self._request_log.record(self.provider_config.name, purpose, body)
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
            f"{self.provider_config.url}/chat/completions",
            json=body,
            headers=self.provider_config.headers,
            timeout=_timeout(timeout, connect=5.0),
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

    def _apply_thinking(self, body: dict[str, object], think: str | None) -> None:
        """Translate the think setting into the request. Base = OpenAI-style
        `reasoning_effort`: a level enables it, "none" actively disables it,
        None sends nothing and leaves the backend's default."""
        if think and self.supports_thinking:
            body["reasoning_effort"] = think

    # ---------- passive introspection (native APIs; defaults = unknown) ----------

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

    def _fetch_context_size(self, model: str) -> int | None:
        return None

    # ---------- helpers for the backends ----------

    def _get_json(self, path: str, *, timeout: float) -> Any | None:
        """GET `<base url>{path}` → parsed JSON; None on any error or
        non-200. For best-effort native-API reads only."""
        try:
            response = httpx.get(
                f"{self.provider_config.base_url}{path}",
                headers=self.provider_config.headers,
                timeout=_timeout(timeout, connect=1.0),
            )
            if response.status_code == 200:
                return response.json()
        except (httpx.HTTPError, OSError, ValueError):
            pass
        return None

    def _post_json(self, path: str, body: dict[str, Any], *, timeout: float) -> Any | None:
        """POST `<base url>{path}` → parsed JSON; None on any error or
        non-200. For best-effort native-API reads only — actions that must
        fail loudly use httpx directly."""
        try:
            response = httpx.post(
                f"{self.provider_config.base_url}{path}",
                json=body,
                headers=self.provider_config.headers,
                timeout=_timeout(timeout, connect=1.0),
            )
            if response.status_code == 200:
                return response.json()
        except (httpx.HTTPError, OSError, ValueError):
            pass
        return None


class LocalSingleClient(OpenAIClient):
    """A single-model engine (llama.cpp, KoboldCpp): the server fronts the
    one model it was launched with — or none — and never loads or unloads.
    Every listed row is loaded and asked for its context window."""

    def _list(self, timeout: float) -> list[ModelInfo]:
        return [
            ModelInfo(name=name, context=self.get_context_size(name), loaded=True)
            for name in self._model_names(timeout)
        ]


class ManagedClient(OpenAIClient, ABC):
    """A local registry (Ollama, omlx, LM Studio) that can load and unload
    models on demand — the actions on top of the passive base. UI offers
    load/unload exactly when a client is one of these."""

    @abstractmethod
    def load_model(self, model: str) -> None: ...

    @abstractmethod
    def unload_model(self, model: str) -> None: ...


class CloudClient(OpenAIClient):
    """A hosted catalog (OpenRouter, NanoGPT): the standard listing with
    each model's context window harvested when the catalog reports one.
    Nothing is sized — there is no disk to weigh — and every row is simply
    available, so all of them list as loaded. Cloud alone has an account
    to bill, so `balance` lives here."""

    local = False
    # Extra query string for the catalog listing, when the service wants
    # one to include the model details.
    _MODELS_QUERY: ClassVar[str] = ""

    def __init__(
        self,
        provider_config: ProviderConfig,
        *,
        request_log: RequestLog | None = None,
        smooth: bool = False,
    ) -> None:
        super().__init__(provider_config, request_log=request_log, smooth=smooth)
        # One failed catalog fetch stops chat-time context lookups for
        # the session — a down catalog must not tax every turn with a
        # timeout; any later successful listing clears the mark.
        self._catalog_down = False

    def balance(self, timeout: float = 10.0) -> str | None:
        """The account balance as the catalog reports it, rendered for a
        human — None when the service will not say."""
        return None

    def _key_works(self, timeout: float) -> bool:
        """Whether the configured api key actually opens the account.
        Catalogs can be public (OpenRouter's is), so a listing alone
        proves nothing; subclasses answer through an authenticated
        endpoint. The base client cannot know and stays permissive."""
        return True

    def _list(self, timeout: float) -> list[ModelInfo]:
        # No key, or one the account rejects → unreachable: rows from a
        # public catalog would only invite a chat that fails with 401.
        if not self.provider_config.api_key:
            raise PermissionError(f"{self.provider_config.name} has no api key")
        if not self._key_works(timeout):
            raise PermissionError(f"{self.provider_config.name} rejected the api key")
        response = httpx.get(
            f"{self.provider_config.url}/models{self._MODELS_QUERY}",
            headers=self.provider_config.headers,
            timeout=_timeout(timeout, connect=2.0),
        )
        response.raise_for_status()
        rows = []
        for entry in response.json().get("data", []):
            context = entry.get("context_length")
            rows.append(
                ModelInfo(
                    name=str(entry["id"]),
                    context=context if isinstance(context, int) and context > 0 else None,
                    loaded=True,
                )
            )
        # The catalog just paid for every window — seed the cache, so
        # chat-time lookups (the assembler's budget, the stats line)
        # never refetch what the picker already carried home.
        for row in rows:
            if row.context:
                self._context_cache[row.name] = row.context
        self._catalog_down = False
        return sorted(rows, key=lambda row: row.name)

    def _fetch_context_size(self, model: str) -> int | None:
        # The catalog is the one source — usually pre-seeded by `_list`;
        # this fetch is the fallback when nothing listed yet.
        if self._catalog_down:
            return None
        try:
            rows = self.models(timeout=5.0)
        except Exception:
            self._catalog_down = True
            return None
        for row in rows:
            if row.name == model:
                return row.context
        return None


def _timeout(total: float, *, connect: float) -> httpx.Timeout:
    """The httpx timeout with its connect phase capped separately. A
    dead-but-routable host — a mistyped LAN IP, a firewalled port — hangs
    the handshake, and a flat timeout lets it hold the whole read budget
    (a five-second launch stall per such provider); a host that is
    listening at all completes the handshake in milliseconds. The read
    budget stays `total`: a slow answer is not a dead host."""
    return httpx.Timeout(total, connect=min(connect, total))
