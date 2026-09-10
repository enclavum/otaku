"""Ollama: the model registry, load/unload, sizes and context windows
via the native /api endpoints, and each model's capabilities from
/api/show; chat rides the OpenAI protocol at /v1. A thinking level
goes out as `reasoning_effort` alone — the one knob the server reads,
clamping what its own scale lacks (xhigh to max) rather than refusing.
"""

import os
from dataclasses import replace

from otaku.providers import http
from otaku.providers.client import (
    ASK_TIMEOUT,
    PROBE_TIMEOUT,
    Capabilities,
    Client,
    Locality,
    ModelInfo,
)
from otaku.settings.providers import ProviderConfig

# The levels the server tells apart; xhigh is clamped to max on its side,
# so it is not a level of its own here.
_LEVELS: frozenset[str] = frozenset({"off", "low", "medium", "high", "max"})


class OllamaClient(Client):
    kind = "ollama"
    label = "Ollama"
    locality = Locality.LOCAL
    env_key = "OLLAMA_API_KEY"

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The first-run section, its host and port detected from
        OLLAMA_HOST — a remote server stays remote."""
        raw = os.environ.get("OLLAMA_HOST") or ""
        host = _parse_host(raw) or "localhost"
        port = _parse_port(raw) or 11434
        return ProviderConfig(
            name=cls.kind,
            url=f"http://{host}:{port}/v1",
            keep_alive="24h",
        )

    @property
    def manages_models(self) -> bool:
        return True

    def load_model(self, model: str) -> None:
        self._generate_nothing(model, keep_alive=self.config.keep_alive or "24h")

    def unload_model(self, model: str) -> None:
        self._generate_nothing(model, keep_alive=0)

    def _generate_nothing(self, model: str, *, keep_alive: str | int) -> None:
        """An empty generation is how the registry is told to load a
        model, and for how long to keep it — zero unloads it."""
        http.post_json(
            f"{self.config.base_url}/api/generate",
            {"model": model, "prompt": "", "stream": False, "keep_alive": keep_alive},
            name=self.config.name,
            headers=self._headers,
            timeout=None,
        )

    def models(self, timeout: float = ASK_TIMEOUT) -> list[ModelInfo]:
        """One row per model: names and sizes from /api/tags, load state
        and the live window from /api/ps — one call each, however long
        the registry. An unloaded model carries NO window: Ollama sizes
        one at load time, from a server-wide default (tiered by VRAM, or
        OLLAMA_CONTEXT_LENGTH) clamped to the card's trained maximum, and
        no endpoint says what that default is. The card's figure is a
        ceiling, not the window — reported as one it budgeted stories
        past what the model could hold. A loaded model missing from the
        registry still belongs in the list."""
        data = http.get_json(
            f"{self.config.base_url}/api/tags",
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
        )
        sizes: dict[str, int] = {}
        entries = data.get("models") if isinstance(data, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            name = entry.get("name") or entry.get("model")
            size = entry.get("size")
            if isinstance(name, str):
                sizes[name] = size if isinstance(size, int) and size > 0 else 0
        running = self._running(timeout=PROBE_TIMEOUT)
        # The listing just paid for every live window — seed the cache,
        # so the budget's ask for a loaded model never refetches it.
        for name, window in running.items():
            if window:
                self._context_sizes[name] = window
        names = sorted(sizes) + sorted(set(running) - set(sizes))
        # The registry's rows carry no capabilities, and a listing is one
        # pass over each endpoint however long the list: the one-model
        # ask (`_model`) reads the card instead.
        return [
            ModelInfo(
                name=name,
                size=sizes.get(name) or None,
                context=running.get(name) or None,
                loaded=name in running,
            )
            for name in names
        ]

    def _model(self, name: str, timeout: float) -> ModelInfo | None:
        row = super()._model(name, timeout)
        return replace(row, capabilities=self._shown_capabilities(name)) if row else None

    def _shown_capabilities(self, model: str) -> Capabilities:
        """What /api/show says of one model — the card's capabilities,
        asked one model at a time."""
        data = http.post_json(
            f"{self.config.base_url}/api/show",
            {"model": model},
            name=self.config.name,
            headers=self._headers,
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        caps = data.get("capabilities") if isinstance(data, dict) else None
        if not isinstance(caps, list):
            return Capabilities()
        thinking = _LEVELS if "thinking" in caps else frozenset[str]()
        return Capabilities(vision="vision" in caps, thinking=thinking)

    def _context_size(self, model: str) -> int | None:
        # Only a loaded model has a window (see `models`): unknown until
        # then, and the base caches nothing for an unknown — so the first
        # turn, the one whose request loads the model, budgets on the
        # assembler's default, and the next ask reads the live figure.
        return self._running(timeout=PROBE_TIMEOUT).get(model) or None

    def _running(self, timeout: float) -> dict[str, int]:
        """The loaded models with their live windows, from /api/ps: name →
        context_length, 0 where the server does not state one."""
        running: dict[str, int] = {}
        data = http.get_json(
            f"{self.config.base_url}/api/ps",
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
            quiet=True,
        )
        entries = data.get("models") if isinstance(data, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            name = entry.get("name") or entry.get("model")
            if not name:
                continue
            window = entry.get("context_length")
            running[str(name)] = window if isinstance(window, int) and window > 0 else 0
        return running


def _parse_host(value: str) -> str | None:
    """The host of a `host:port`, `http://host[:port]`, or bare `host`
    string; None when there is none (`:port`, a bare port, empty)."""
    trimmed = value.strip()
    for scheme in ("http://", "https://"):
        if trimmed.startswith(scheme):
            trimmed = trimmed[len(scheme) :]
            break
    trimmed = trimmed.split("/", 1)[0]
    host, colon, _ = trimmed.rpartition(":")
    if not colon:
        return None if not trimmed or trimmed.isdigit() else trimmed
    return host or None


def _parse_port(value: str) -> int | None:
    """The port of a `host:port`, `:port`, `http://host:port`, or bare
    `port` string; None when the tail is not a valid port."""
    try:
        port = int(value.rsplit(":", 1)[-1].strip())
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None
