"""Ollama: the registry, load and unload, sizes and the loaded windows
via the native /api endpoints, each model's capabilities and ceiling
from its card at /api/show; chat rides the OpenAI protocol at /v1. A
reasoning effort goes out as `reasoning_effort` alone, the one knob the
server reads, which clamps xhigh to max rather than refusing.
"""

import os
from dataclasses import replace
from typing import Any

from otaku.providers import http
from otaku.providers.http import PROBE_TIMEOUT, positive_int
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.models import Capabilities, Listing, ModelInfo, ModelState, OpenAIModels
from otaku.settings.providers import ProviderConfig

# The efforts the server tells apart; xhigh is clamped to max on its
# side, so it is not one of its own here.
_EFFORTS: frozenset[str] = frozenset({"none", "low", "medium", "high", "max"})


class OllamaModels(OpenAIModels):
    @property
    def can_manage(self) -> bool:
        return True

    def load(self, model: str) -> None:
        self._generate_nothing(model, keep_alive=self._config.keep_alive or "24h")

    def unload(self, model: str) -> None:
        self._generate_nothing(model, keep_alive=0)

    # ---------- the hooks ----------

    def _list(self, timeout: float) -> Listing:
        """Names and sizes from /api/tags, state and window from /api/ps,
        one call each however long the registry. A tags entry names
        capabilities too, but not the card's (a server here said of
        Gemma 4 "completion, tools, thinking" where the card adds
        vision), so only what an entry cannot get wrong is read off it:
        an embedding model has no "completion" and plays no story. A
        loaded model missing from the registry still belongs in the
        list."""
        data = http.get_json(
            f"{self._config.base_url}/api/tags",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
        )
        sizes: dict[str, int | None] = {}
        entries = data.get("models") if isinstance(data, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            name = entry.get("name") or entry.get("model")
            if not isinstance(name, str):
                continue
            caps = entry.get("capabilities")
            if isinstance(caps, list) and "completion" not in caps:
                continue
            sizes[name] = positive_int(entry.get("size"))
        running = self._running() or {}
        names = sorted(sizes) + sorted(set(running) - set(sizes))
        return [
            ModelInfo(
                name=name,
                size=sizes.get(name),
                max_context_loaded=running.get(name),
                state=ModelState.LOADED if name in running else ModelState.UNLOADED,
            )
            for name in names
        ]

    def _enhance(self, model: ModelInfo, timeout: float) -> ModelInfo:
        """The card: capabilities and the trained context length, the
        ceiling — never the window, which Ollama sizes at load time from
        a server-wide default clamped to it."""
        data = http.post_json(
            f"{self._config.base_url}/api/show",
            {"model": model.name},
            name=self._config.name,
            headers=self._auth.headers,
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        if not isinstance(data, dict):
            return model
        caps = data.get("capabilities")
        info = data.get("model_info")
        return replace(
            model,
            capabilities=_capabilities_of(caps) if isinstance(caps, list) else Capabilities(),
            max_context_catalogue=_context_length_of(info) if isinstance(info, dict) else None,
            checked=True,
        )

    def _state(self, name: str) -> tuple[ModelState, int | None] | None:
        running = self._running()
        if running is None:
            return None
        if name in running:
            return ModelState.LOADED, running[name]
        return ModelState.UNLOADED, None

    # ---------- the native surface ----------

    def _generate_nothing(self, model: str, *, keep_alive: str | int) -> None:
        """An empty generation is how the registry is told to load a
        model, and for how long to keep it — zero unloads it."""
        http.post_json(
            f"{self._config.base_url}/api/generate",
            {"model": model, "prompt": "", "stream": False, "keep_alive": keep_alive},
            name=self._config.name,
            headers=self._auth.headers,
            timeout=None,
        )

    def _running(self) -> dict[str, int | None] | None:
        """The loaded models with their windows, from /api/ps: name →
        context_length, None where the server states none. None
        altogether when the server did not answer."""
        data = http.get_json(
            f"{self._config.base_url}/api/ps",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        if not isinstance(data, dict):
            return None
        running: dict[str, int | None] = {}
        entries = data.get("models")
        for entry in entries if isinstance(entries, list) else []:
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str):
                running[name] = positive_int(entry.get("context_length"))
        return running


class OllamaClient(OpenAIClient):
    id = "ollama"
    label = "Ollama"
    locality = Locality.LOCAL
    env_key = "OLLAMA_API_KEY"
    models_class = OllamaModels

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The first-run section, its host and port from OLLAMA_HOST — a
        remote server stays remote."""
        scheme, host, port, path = _parse_ollama_host(os.environ.get("OLLAMA_HOST") or "")
        return ProviderConfig(
            name=cls.id,
            url=f"{scheme}://{host}:{port}{path}/v1",
            keep_alive="24h",
        )


def _capabilities_of(caps: list[Any]) -> Capabilities:
    """A card's capability words as ours. No raw text wire: Ollama's
    /v1/completions wraps the prompt as one chat turn and thinks unseen.
    Decoding is constrained server-side (`format`) for every model."""
    reasoning = _EFFORTS if "thinking" in caps else frozenset[str]()
    return Capabilities(
        vision="vision" in caps, reasoning=reasoning, text_completion=False, structured_output=True
    )


def _context_length_of(info: dict[str, Any]) -> int | None:
    """The trained context length a card's model_info states, under the
    architecture's own key ("gemma3.context_length") — or any key so
    named, when the architecture is not stated."""
    arch = info.get("general.architecture")
    keyed = info.get(f"{arch}.context_length") if isinstance(arch, str) else None
    if keyed is None:
        keyed = next((v for k, v in info.items() if k.endswith(".context_length")), None)
    return positive_int(keyed)


def _parse_ollama_host(value: str) -> tuple[str, str, int, str]:
    """OLLAMA_HOST read the way Ollama reads it: `host:port`, `:port`,
    a bare `host` (digits included — a bare number is a host to Ollama,
    not a port), a bare IPv6 address (bracketed for the url), or
    `scheme://host[:port][/path]`. The scheme is kept; a port left out
    is Ollama's 11434, unless a scheme was written, where it is the
    scheme's own (80, 443); a path is kept, as Ollama's client keeps it;
    surrounding quotes are shed; an empty or invalid value is the local
    default. Returns (scheme, host, port, path)."""
    scheme, host, port, path = "http", "localhost", 11434, ""
    trimmed = value.strip().strip("\"'")
    for prefix in ("http://", "https://"):
        if trimmed.lower().startswith(prefix):
            scheme = prefix[:-3]
            port = 443 if scheme == "https" else 80
            trimmed = trimmed[len(prefix) :]
            break
    trimmed, _, rest = trimmed.partition("/")
    if rest.strip("/"):
        path = "/" + rest.strip("/")
    written = ""
    if trimmed.startswith("["):
        # A bracketed IPv6 address, with or without a port.
        close = trimmed.find("]")
        if close > 0:
            host = trimmed[: close + 1]
            written = trimmed[close + 1 :].lstrip(":")
    elif trimmed.count(":") > 1:
        host = f"[{trimmed}]"  # a bare IPv6 address
    else:
        head, colon, tail = trimmed.rpartition(":")
        if colon:
            host, written = head or host, tail
        else:
            host = trimmed or host
    if written:
        try:
            number = int(written)
        except ValueError:
            number = 0
        port = number if 1 <= number <= 65535 else port
    return scheme, host, port, path
