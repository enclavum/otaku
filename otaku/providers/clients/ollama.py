"""Ollama: the registry, load and unload, sizes and the loaded context sizes
via the native /api endpoints, each model's capabilities and its own
max context from its card at /api/show — the registry row states the
max context too, on a model a recent Ollama pulled; chat rides the
OpenAI protocol at /v1, which reads the protocol's parameters and nothing beyond
them — `top_k`, `min_p` and `repetition_penalty` go out and are
dropped unread, as the base's `supported_params` says. A reasoning effort
goes out as `reasoning_effort` alone, the one knob the server reads;
it takes none, low, medium, high and max, and answers 400 to any
other word, which the take then sends again without the knob. A model
served by ollama.com (`remote_host` on its entry) is listed as the
registry describes it, its state unknown and nothing to load: every
request for it is proxied.
"""

import contextlib
import ipaddress
import os
import time
from dataclasses import replace
from typing import Any

from otaku.providers.errors import ProviderError
from otaku.providers.http import PROBE_TIMEOUT, Http, positive_int
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.models import (
    Listing,
    ModelCapabilities,
    ModelInfo,
    ModelState,
    OpenAIModels,
)
from otaku.settings.providers import ProviderConfig

# The words the server accepts; any other is a 400.
_EFFORTS: frozenset[str] = frozenset({"none", "low", "medium", "high", "max"})
_UNLOAD_WAIT_SECONDS = 5.0  # an unload is answered before it is done
_UNLOAD_POLL_SECONDS = 0.25


class OllamaModels(OpenAIModels):
    def __init__(self, config: ProviderConfig, auth: OpenAIAuth, http: Http) -> None:
        super().__init__(config, auth, http)
        self._remote: set[str] = set()  # served by ollama.com, as of the last listing
        self._carded: set[str] = set()  # whose card has answered, this session

    @property
    def can_manage(self) -> bool:
        return True

    def load(self, model: str) -> None:
        self._refuse_remote(model)
        self._generate_nothing(model, keep_alive=self._config.keep_alive or "24h", purpose="load")

    def unload(self, model: str) -> None:
        # Answered as soon as the runner is told to expire; the scheduler
        # unloads after, so the picker's read-back is waited for.
        self._refuse_remote(model)
        self._generate_nothing(model, keep_alive=0, purpose="unload")
        deadline = time.monotonic() + _UNLOAD_WAIT_SECONDS
        names = {model, self._canonical(model)}  # the door takes either spelling
        while time.monotonic() < deadline:
            running = self._running(self._http)
            if running is not None and names.isdisjoint(running):
                return
            time.sleep(_UNLOAD_POLL_SECONDS)

    # ---------- the hooks ----------

    def _list(self, http: Http) -> Listing:
        """Names, sizes and the model's own max context from /api/tags,
        state and loaded context size from /api/ps, one call each
        however long the registry. A tags entry names capabilities too,
        but not the card's (a server here said of Gemma 4 "completion,
        tools, thinking" where the card adds vision), so only what an
        entry cannot get wrong is read off it: an embedding model has
        no "completion" and plays no story, wherever it is served; and
        `details.context_length` is the trained length, which a recent
        pull records and an older one leaves for the card to say. A
        model with a `remote_host` is ollama.com's: no runner ever lists
        it, its card is an internet round-trip, so the entry's own word
        is taken and its state stays unknown. A loaded model missing
        from the registry still belongs in the list; a listed one that
        plays no story does not come back through ps. A ps probe that
        did not answer leaves every state unknown."""
        data = http.get(f"{self._config.base_url}/api/tags")
        seen: set[str] = set()
        sizes: dict[str, int | None] = {}
        contexts: dict[str, int | None] = {}
        remote: dict[str, ModelInfo] = {}
        entries = data.get("models") if isinstance(data, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            name = entry.get("name") or entry.get("model")
            if not isinstance(name, str):
                continue
            seen.add(name)
            caps = entry.get("capabilities")
            if isinstance(caps, list) and "completion" not in caps:
                continue
            details = entry.get("details")
            context = (
                positive_int(details.get("context_length")) if isinstance(details, dict) else None
            )
            if entry.get("remote_host"):
                remote[name] = ModelInfo(
                    name=name,
                    max_context_catalogue=context,
                    capabilities=self._capabilities_of(caps) if isinstance(caps, list) else None,
                )
                continue
            sizes[name] = positive_int(entry.get("size"))
            contexts[name] = context
        self._remote = set(remote)
        running = self._running(http)
        names = sorted(sizes) + sorted(set(running or {}) - seen)
        local = [
            ModelInfo(
                name=name,
                size=sizes.get(name),
                max_context_catalogue=contexts.get(name),
                max_context_loaded=running.get(name) if running else None,
                state=self._state_of(running, name),
            )
            for name in names
        ]
        return local + [remote[name] for name in sorted(remote)]

    def _get(self, name: str, http: Http) -> ModelInfo | None:
        """/api/ps for the instance, and — once a session, the first time
        it answers — the card: capabilities and the trained context
        length, the model's own max context, never the loaded size,
        which Ollama sets at load time from a server-wide default
        clamped to it. The model's view carries the whole budget: a cold
        card reads the model's header first. A model ollama.com serves
        has neither: nothing runs here for it."""
        if name in self._remote:
            return ModelInfo(name=name, state=ModelState.UNKNOWN)
        running = self._running(http)
        if running is None:
            return None
        fresh = ModelInfo(
            name=name, max_context_loaded=running.get(name), state=self._state_of(running, name)
        )
        if name in self._carded:
            return fresh
        data = http.post(f"{self._config.base_url}/api/show", {"model": name}, quiet=True)
        if not isinstance(data, dict):
            return fresh
        caps = data.get("capabilities")
        info = data.get("model_info")
        # The trained length is under the architecture's own key
        # ("gemma3.context_length") — or any key so named, when the
        # architecture is not stated.
        max_context_catalogue = None
        if isinstance(info, dict):
            arch = info.get("general.architecture")
            keyed = info.get(f"{arch}.context_length") if isinstance(arch, str) else None
            if keyed is None:
                keyed = next((v for k, v in info.items() if k.endswith(".context_length")), None)
            max_context_catalogue = positive_int(keyed)
        self._carded.add(name)
        return replace(
            fresh,
            capabilities=self._capabilities_of(caps)
            if isinstance(caps, list)
            else ModelCapabilities(),
            max_context_catalogue=max_context_catalogue,
        )

    def _canonical(self, name: str) -> str:
        # A name without a tag is its ":latest" one, as Ollama reads it;
        # the tag follows the last slash, so a registry's port is none.
        _, _, tail = name.rpartition("/")
        return name if ":" in tail else f"{name}:latest"

    # ---------- the native surface ----------

    def _refuse_remote(self, model: str) -> None:
        if model in self._remote or self._canonical(model) in self._remote:
            raise ProviderError(f"{model} is served by ollama.com; there is nothing to load.")

    def _generate_nothing(self, model: str, *, keep_alive: str | int, purpose: str) -> None:
        """An empty generation is how the registry is told to load a
        model, and for how long to keep it — zero unloads it."""
        self._http.post(
            f"{self._config.base_url}/api/generate",
            {"model": model, "prompt": "", "stream": False, "keep_alive": keep_alive},
            purpose=purpose,
        )

    def _running(self, http: Http) -> dict[str, int | None] | None:
        """The loaded models with their context sizes, from /api/ps: name →
        context_length, None where the server states none. None
        altogether when the server did not answer. `http` is the
        view of the ask that is running, the listing's or the model's."""
        data = http.get(f"{self._config.base_url}/api/ps", timeout=PROBE_TIMEOUT, quiet=True)
        if not isinstance(data, dict):
            return None
        running: dict[str, int | None] = {}
        entries = data.get("models")
        for entry in entries if isinstance(entries, list) else []:
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str):
                running[name] = positive_int(entry.get("context_length"))
        return running

    def _state_of(self, running: dict[str, int | None] | None, name: str) -> ModelState:
        """Loaded or not by the ps listing; unknown when ps did not answer."""
        if running is None:
            return ModelState.UNKNOWN
        return ModelState.LOADED if name in running else ModelState.UNLOADED

    def _capabilities_of(self, caps: list[Any]) -> ModelCapabilities:
        """A card's capability words as ours. No raw text wire: Ollama's
        /v1/completions wraps the prompt as one chat turn and thinks
        unseen. Decoding is constrained server-side (`format`) for every
        model."""
        reasoning = _EFFORTS if "thinking" in caps else frozenset[str]()
        return ModelCapabilities(
            vision="vision" in caps,
            audio="audio" in caps,
            reasoning=reasoning,
            text_completion=False,
            structured_output=True,
        )


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
    # A bind-all address (0.0.0.0, ::) is the server's to listen on;
    # Ollama's own consumers dial the loopback in its place.
    with contextlib.suppress(ValueError):
        address = ipaddress.ip_address(host.strip("[]"))
        if address.is_unspecified:
            host = "127.0.0.1" if address.version == 4 else "[::1]"
    return scheme, host, port, path
