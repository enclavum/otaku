"""OmlxClient — load/unload/sizes/loaded-state via omlx's native
/v1/models/status registry and /v1/models/{id}/{load,unload} endpoints.

omlx is an MLX model server that speaks OpenAI-compat for chat but adds
its own model-management surface under /v1/models. Unlike a bare
OpenAI-compat endpoint it has a real load/unload concept, so it gets a
dedicated client instead of falling through to ProviderClient.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import httpx

from otaku.client.base import (
    Chunk,
    ContentDelta,
    FinalStats,
    ProviderClient,
    ThinkingDelta,
    base_url,
    get_json,
    headers,
    provider_config_section,
    read_home_json,
)
from otaku.config import Provider
from otaku.storage.store import Message


class OmlxClient(ProviderClient):
    """omlx MLX server (model registry + load/unload at /v1/models)."""

    kind = "omlx"
    # Smoothing: omlx batches/merges tokens before flushing (its vLLM-style
    # output collector), so the raw stream arrives in bursts. When
    # `[providers.omlx].smoothen_streaming` is on, `chat_stream` re-times bursts into
    # steady character-by-character output. Tunables (overridable per-instance
    # in tests): drain cadence and the target buffer-drain lag.
    _SMOOTH_TICK = 0.02  # seconds between drain steps
    _SMOOTH_WINDOW = 0.15  # aim to empty the buffer over ~this long → ~constant lag

    @classmethod
    def matches(cls, provider: Provider) -> bool:
        data = get_json(provider, "/v1/models/status", timeout=0.5)
        if not isinstance(data, dict):
            return False
        models = data.get("models")
        # Confirm the shape — items carry a `loaded` flag. (LM Studio
        # answers 200 with an error body for unknown paths, so a status
        # check alone isn't enough.)
        return isinstance(models, list) and (not models or "loaded" in (models[0] or {}))

    @classmethod
    def default_config_section(cls) -> str:
        settings = read_home_json(".omlx/settings.json")
        server = settings.get("server")
        port = server.get("port") if isinstance(server, dict) else None
        auth = settings.get("auth")
        key = auth.get("api_key") if isinstance(auth, dict) else None
        return provider_config_section(
            "omlx",
            port if isinstance(port, int) else 8000,
            api_key=str(key) if key else "",
            extra=("supports_thinking = false", "smoothen_streaming = true"),
        )

    def _status_models(self, timeout: float = 5.0) -> list[dict[str, Any]]:
        data = get_json(self.provider, "/v1/models/status", timeout=timeout)
        if isinstance(data, dict):
            models = data.get("models")
            if isinstance(models, list):
                return models
        return []

    def loaded_models(self, timeout: float = 1.5) -> set[str]:
        out: set[str] = set()
        for m in self._status_models(timeout):
            mid = m.get("id")
            if isinstance(mid, str) and m.get("loaded"):
                out.add(mid)
        return out

    def model_sizes(self, timeout: float = 5.0) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self._status_models(timeout):
            mid = m.get("id")
            size = m.get("actual_size") or m.get("estimated_size")
            if isinstance(mid, str) and isinstance(size, int) and size > 0:
                out[mid] = size
        return out

    def load_model(self, model: str) -> None:
        r = httpx.post(
            f"{base_url(self.provider)}/v1/models/{quote(model, safe='')}/load",
            headers=headers(self.provider),
            timeout=None,
        )
        r.raise_for_status()

    def unload_model(self, model: str) -> None:
        r = httpx.post(
            f"{base_url(self.provider)}/v1/models/{quote(model, safe='')}/unload",
            headers=headers(self.provider),
            timeout=None,
        )
        r.raise_for_status()

    def _apply_thinking(self, body: dict[str, object], think: str | None) -> None:
        # omlx ignores `reasoning_effort`; thinking is gated by the chat
        # template's `enable_thinking` flag. "none" disables, any level
        # enables. `None` leaves the model's template default in place.
        # Sent regardless of `supports_thinking` so "off" always takes —
        # turning it on is still gated by `/set think`.
        if think is None:
            return
        body["chat_template_kwargs"] = {"enable_thinking": think != "none"}

    def _fetch_context_size(self, model: str) -> int | None:
        for m in self._status_models(timeout=1.5):
            if m.get("id") == model and isinstance(m.get("max_context_window"), int):
                return int(m["max_context_window"])
        return None

    # ---------- output smoothing ----------

    def chat_stream(
        self,
        model: str,
        messages: list[Message],
        params: dict[str, object],
        think: str | None = None,
        timeout: float = 600.0,
    ) -> Iterator[Chunk]:
        base = super().chat_stream(model, messages, params, think=think, timeout=timeout)
        if not self.provider.smoothen_streaming:
            yield from base
            return
        yield from self._smooth(base)

    def _drain_count(self, pending: int) -> int:
        """How many buffered chars to emit this tick so the buffer would empty
        over ~`_SMOOTH_WINDOW` — proportional to how far the producer is ahead
        (so a bigger backlog drains faster), but at least one."""
        return max(1, math.ceil(pending * self._SMOOTH_TICK / self._SMOOTH_WINDOW))

    def _smooth(self, base: Iterator[Chunk]) -> Iterator[Chunk]:
        """Re-time a bursty stream into smooth output. A pump thread drains
        `base` at full speed into a char buffer — preserving real arrival timing
        for the FinalStats — while this generator emits from the buffer at a
        paced rate. Thinking deltas pass through immediately; FinalStats (or a
        relayed error) come after the buffered text is fully drained."""
        buf: list[str] = []
        thinking: deque[ThinkingDelta] = deque()
        lock = threading.Lock()
        done = threading.Event()
        final: list[FinalStats | None] = [None]
        error: list[Exception | None] = [None]

        def pump() -> None:
            try:
                for chunk in base:
                    if isinstance(chunk, ContentDelta):
                        with lock:
                            buf.extend(chunk.text)
                    elif isinstance(chunk, ThinkingDelta):
                        with lock:
                            thinking.append(chunk)
                    elif isinstance(chunk, FinalStats):
                        final[0] = chunk
                    if done.is_set():  # consumer aborted → stop reading
                        break
            except Exception as e:  # relayed to the consumer after draining
                error[0] = e
            finally:
                # `base` is owned here, so closing it (→ dropping the HTTP stream)
                # is safe. Generators have close(); guard for plain iterators.
                closer = getattr(base, "close", None)
                if closer is not None:
                    closer()
                done.set()

        worker = threading.Thread(target=pump, daemon=True)
        worker.start()
        try:
            while True:
                out_thinking: ThinkingDelta | None = None
                out_text = ""
                with lock:
                    if thinking:
                        out_thinking = thinking.popleft()
                    elif buf:
                        n = self._drain_count(len(buf))
                        out_text = "".join(buf[:n])
                        del buf[:n]
                    finished = done.is_set() and not buf and not thinking
                if out_thinking is not None:
                    yield out_thinking
                    continue
                if out_text:
                    yield ContentDelta(out_text)
                if finished:
                    break
                time.sleep(self._SMOOTH_TICK)
            if error[0] is not None:
                raise error[0]
            if final[0] is not None:
                yield final[0]
        finally:
            done.set()  # signal the pump to stop if the consumer aborted mid-stream
