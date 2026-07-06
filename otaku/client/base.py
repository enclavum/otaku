"""ProviderClient base class + streaming chunk types.

`ProviderClient` is the OpenAI-compatible client (list_models +
chat_stream) and the base class for all backend-specific subclasses
(see otaku.client.ollama, otaku.client.lmstudio). The default load/unload/
sizes/loaded-state hooks raise / return empty so a generic OpenAI-compat
endpoint just works without exposing those operations.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import httpx

from otaku.config import Provider
from otaku.storage.store import Message


@dataclass(frozen=True)
class ContentDelta:
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    text: str


@dataclass(frozen=True)
class FinalStats:
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_seconds: float
    # Loaded context window size for this model (None when the backend
    # doesn't expose it).
    context_max: int | None = None
    # Decode-only elapsed: first emitted token → end of stream, excluding
    # prefill + time-to-first-token. None when no tokens were emitted.
    # Used as the tok/s denominator so the rate reflects generation speed.
    generation_seconds: float | None = None


Chunk = ContentDelta | ThinkingDelta | FinalStats


def base_url(provider: Provider) -> str:
    """Provider URL without the trailing /v1 (used by ollama/lmstudio
    native endpoints that live outside the OpenAI-compat namespace)."""
    base = provider.url
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def headers(provider: Provider) -> dict[str, str]:
    """Auth headers for the provider — empty when no api_key is set.
    httpx auto-sets Content-Type: application/json whenever `json=` is
    passed, so we don't need to declare it here.
    """
    if provider.api_key:
        return {"Authorization": f"Bearer {provider.api_key}"}
    return {}


# Hoisted out of the `except (...)` clause: ruff format strips the parens,
# leaving `except httpx.HTTPError, OSError, ValueError:` which is 3.14-only
# syntax (a tuple parses on every supported Python).
_REQUEST_ERRORS = (httpx.HTTPError, OSError, ValueError)


def get_json(provider: Provider, path: str, *, timeout: float = 5.0) -> Any | None:
    """GET `<provider base, /v1 stripped>{path}` and return parsed JSON.
    Returns None on any network error or non-200 response — callers that
    need to distinguish should not use this helper.
    """
    try:
        r = httpx.get(
            f"{base_url(provider)}{path}",
            headers=headers(provider),
            timeout=timeout,
        )
        if r.status_code == 200:
            return r.json()
    except _REQUEST_ERRORS:
        pass
    return None


# ---------- first-run config detection (shared by the subclasses) ----------


def read_home_json(relative: str) -> dict[str, Any]:
    """Parse a JSON object at `~/<relative>`; empty dict on any failure. For
    reading a local engine's own settings file during first-run detection."""
    try:
        obj = json.loads((Path.home() / relative).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def port_from_listen(s: str) -> int | None:
    """Port from a `host:port`, `:port`, `http://host:port`, or bare `port`
    string; None if the tail isn't a valid 1-65535 port."""
    try:
        port = int(s.rsplit(":", 1)[-1].strip())
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def provider_config_section(
    name: str, port: int, *, api_key: str = "", extra: Sequence[str] = ()
) -> str:
    """A first-run `[providers.NAME]` TOML section pointing at localhost:`port`."""
    lines = [
        f"[providers.{name}]",
        f'url = "http://localhost:{port}/v1"',
        f'api_key = "{api_key}"',
        *extra,
    ]
    return "\n".join(lines) + "\n"


class ProviderClient:
    """OpenAI-compatible HTTP client. Doubles as the base class for
    provider-specific subclasses which override the load/unload/sizes/
    loaded-state hooks. Used directly as the catch-all for any URL that
    doesn't match a specialised subclass.
    """

    kind: ClassVar[str] = "openai"

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self._context_cache: dict[str, int | None] = {}

    @classmethod
    def matches(cls, provider: Provider) -> bool:
        """True if this class should handle `provider`. The base class
        accepts everything; subclasses override with a real URL probe.
        """
        return True

    @classmethod
    def default_config_section(cls) -> str | None:
        """The `[providers.NAME]` TOML section this backend contributes to a
        first-run config.toml, with its port (and api key) auto-detected from
        the machine — see `read_home_json` / `port_from_listen`. None for the
        generic OpenAI-compat catch-all, which has no built-in default."""
        return None

    # ---------- OpenAI-compat (shared) ----------

    def list_models(self, timeout: float = 10.0) -> list[str]:
        r = httpx.get(
            f"{self.provider.url}/models",
            headers=headers(self.provider),
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        return sorted(str(m["id"]) for m in data.get("data", []))

    def chat_stream(
        self,
        model: str,
        messages: list[Message],
        params: dict[str, object],
        think: str | None = None,
        timeout: float = 600.0,
    ) -> Iterator[Chunk]:
        body: dict[str, object] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            **params,
        }
        self._apply_thinking(body, think)

        start = time.monotonic()
        first_token_at: float | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None

        with httpx.stream(
            "POST",
            f"{self.provider.url}/chat/completions",
            json=body,
            headers=headers(self.provider),
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                usage = obj.get("usage")
                if isinstance(usage, dict):
                    prompt_tokens = usage.get("prompt_tokens")
                    completion_tokens = usage.get("completion_tokens")

                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                think_text = delta.get("reasoning_content") or delta.get("reasoning")
                if think_text:
                    if first_token_at is None:
                        first_token_at = time.monotonic()
                    yield ThinkingDelta(text=str(think_text))
                content = delta.get("content")
                if content:
                    if first_token_at is None:
                        first_token_at = time.monotonic()
                    yield ContentDelta(text=str(content))

        end = time.monotonic()
        # Decode-only span: first emitted token → end, excluding prefill and
        # time-to-first-token. The OpenAI-compat usage block carries no
        # eval timing, so this client-side measurement is the closest we
        # get to the backend's true generation rate.
        generation_seconds = (end - first_token_at) if first_token_at is not None else None
        yield FinalStats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=end - start,
            context_max=self.context_size(model),
            generation_seconds=generation_seconds,
        )

    # ---------- provider-specific (default = unsupported) ----------

    def _apply_thinking(self, body: dict[str, object], think: str | None) -> None:
        """Translate otaku's think setting into the backend's request field.

        Base = OpenAI-style `reasoning_effort`: "low"/"medium"/"high"/"max"
        enable thinking at that level, "none" actively disables it (Ollama's
        compat layer maps it to `think: false`). `None` (or an unsupported
        provider) sends nothing, leaving the backend's model default.
        Subclasses override for backends that use a different mechanism.
        """
        if think and self.provider.supports_thinking:
            body["reasoning_effort"] = think

    def loaded_models(self, timeout: float = 1.5) -> set[str]:
        return set()

    def model_sizes(self, timeout: float = 5.0) -> dict[str, int]:
        return {}

    def load_model(self, model: str) -> None:
        raise NotImplementedError(f"load not supported by {self.kind!r} provider")

    def unload_model(self, model: str) -> None:
        raise NotImplementedError(f"unload not supported by {self.kind!r} provider")

    def context_size(self, model: str) -> int | None:
        """Currently-loaded context window for `model`, or None when the
        backend doesn't expose it. Result is cached per-instance.
        """
        if model in self._context_cache:
            return self._context_cache[model]
        result = self._fetch_context_size(model)
        self._context_cache[model] = result
        return result

    def _fetch_context_size(self, model: str) -> int | None:
        """Override in subclasses that have a way to ask the backend."""
        return None
