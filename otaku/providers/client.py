"""The client: one class every engine is a subclass of, speaking the
OpenAI protocol over `http` with `wire`'s bodies — `/models` to list,
streaming `/chat/completions` and `/completions` to generate — and,
where an engine has a native API of its own, a hook per fact it adds.

What a subclass DECLARES is class knowledge, the facts true of its
engine before any server answers: the section name that selects it and
how the panel captions it, where its server runs (`Locality`), the
environment variable its key falls back to, whether it honours prompt
cache breakpoints, which request fields carry a thinking level, and
whether it counts tokens. What it OVERRIDES are the hooks declared
here, each with a base answer — the listing, the one-model row, the
context size, the token counts, the account, the load and unload — so
an engine implements only what it has and the extension surface reads
in one place. Nothing is inherited between engines: shared code is a
helper here, called by name.

A model's facts ride on its listing row (`ModelInfo.capabilities`),
read from what the engine reports and never assumed from the engine
alone: whether it takes images, which thinking levels it honours,
whether the text wire exists for it. Unknown reads as ALLOWED — a
frontend offers the feature, and the request's own failure, in this
package's sentence, is the answer.

Streams yield typed chunks: `Thinking` and `Text` deltas, then one
`Stats`. Bursty output is re-timed into an even flow when smoothing is
on (`smoothing`); a call nobody watches passes watched=False and skips
it. Closing a stream mid-way closes the connection, which stops the
server's generation — cancel-and-keep is the consumer's to do.
"""

import enum
import os
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from otaku.formatting import Money
from otaku.providers import http, smoothing, wire
from otaku.providers.errors import (
    DeclinedError,
    ProviderError,
    StatusError,
    UnauthorizedError,
)
from otaku.providers.wire import (
    ALL_THINKING_LEVELS,
    Chunk,
    Image,
    Stats,
    Text,
    Thinking,
    WireMessage,
)
from otaku.settings.providers import ProviderConfig

# What a stream reads off each frame: (thinking, text) deltas.
_DeltaReader = Callable[[dict[str, Any]], tuple[str, str]]

# How long an ask may take, by what it is. A listing, a count or a
# balance is one question with one answer (ASK); a native best-effort
# read — a window, a card, a status — is never worth a turn's wait
# (PROBE); a listing in the picker's fan-out or at chat time is bounded
# so one dead provider costs at most this (LISTING); a reply is read
# chunk by chunk and may take what a model takes (REPLY).
ASK_TIMEOUT = 10.0
PROBE_TIMEOUT = 1.5
LISTING_TIMEOUT = 5.0
REPLY_TIMEOUT = 600.0


class Locality(enum.Enum):
    """Where a provider's server runs, as far as its CLIENT can tell —
    class knowledge. An engine's client knows (llama.cpp is on this
    machine, OpenRouter is not); the generic provider is a url and
    cannot. Every reader picks its safe side for UNKNOWN: what costs
    money or waits on the internet (a warm-up, a catalog fetch at a
    header's speed) treats it as REMOTE, what edits (the url) treats it
    as LOCAL, and a caption says neither."""

    LOCAL = "local"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class KeySource(enum.Enum):
    """Where the key a client sends came from: the provider's SECTION
    (saved in the file, or typed in the panel for the session) or the
    engine's environment variable; None where there is no key. A
    section's key wins over the variable — what somebody typed beats
    what the shell carries — and clearing it uncovers the variable."""

    SECTION = "section"
    ENV = "env"


@dataclass(frozen=True)
class Capabilities:
    """What one model can do, as its provider reports it — an engine
    fills in what it could read and the defaults say the rest is
    allowed. `thinking` is the set of levels the model honours, "off"
    among them when it can be switched off and absent when its
    reasoning is mandatory; empty means nothing sent reaches it. A new
    capability is a field here, and a line in the decoding of each
    engine that reports it."""

    vision: bool = True
    thinking: frozenset[str] = ALL_THINKING_LEVELS
    completion: bool = True  # the text wire exists for the model


@dataclass(frozen=True)
class ModelInfo:
    """One model as its provider reports it — the row every listing
    returns, filled as far as the engine's native API can see. The
    capabilities are None where the listing did not read them (a plain
    /models listing; Ollama's registry, whose card is read by the
    one-model ask), which a reader treats as allowed."""

    name: str
    size: int | None = None  # bytes on disk; local engines only
    context: int | None = None  # the model's context size, when reported
    capabilities: Capabilities | None = None
    loaded: bool = False


class RequestSink(Protocol):
    """Where requests and their answers are recorded — the injected
    seam the session's request log satisfies. `record_request` returns
    the id the answer is later filed under; `record_answer` files what
    the stream came to: the outcome, the timings and token counts on
    the envelope, the answer's text and thinking."""

    def record_request(self, provider: str, purpose: str, body: dict[str, object]) -> str: ...

    def record_answer(
        self,
        provider: str,
        purpose: str,
        request_id: str,
        *,
        outcome: str,
        seconds: float,
        first_token_seconds: float | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cached_tokens: int | None,
        text: str,
        thinking: str = "",
    ) -> None: ...


class Client:
    # ---------- class knowledge: what is true of the engine ----------

    kind: ClassVar[str]  # the section name that selects this client
    label: ClassVar[str]  # how the provider panel captions it
    locality: ClassVar[Locality] = Locality.UNKNOWN
    # The environment variable a missing key is read from; "" reads none.
    env_key: ClassVar[str] = ""
    # Whether the engine honours explicit prompt-cache breakpoints
    # (`cache_control` on content parts — Anthropic's marking, forwarded
    # by OpenRouter). The section's `prompt_cache` key modulates it
    # ("off" | "5m" | "1h"), never enables it where the engine cannot.
    cache_markers: ClassVar[bool] = False
    # The request fields a thinking level goes out on — the ones this
    # engine reads. The protocol's own is `reasoning_effort`; the local
    # engines read the chat template's flag as well, or instead; an
    # engine that reads neither declares none and receives nothing.
    thinking_knobs: ClassVar[frozenset[str]] = frozenset({wire.THINKING_EFFORT_KNOB})
    # The same for the TEXT wire, where no template stands between the
    # prompt and the model: the flag has nowhere to go, and the effort
    # is read by fewer engines — each declares what it reads there, and
    # the base, nothing.
    text_thinking_knobs: ClassVar[frozenset[str]] = frozenset()
    # Whether the engine counts tokens (`count_chat_tokens`,
    # `count_text_tokens`) — the engine's fact, not a model's, so it is
    # asked of the client, which every reader of a count holds.
    counts_tokens: ClassVar[bool] = False

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The engine's default provider section: what the panel shows
        before an engine is configured, and what first run writes for
        the local engines (each client says what it detects). The plain
        OpenAI client has no natural endpoint — a generic provider is
        configured by hand. No key: the environment variable's is read
        by the client at request time (`api_key`), never written into a
        section."""
        return ProviderConfig(name=cls.kind, url="")

    @classmethod
    def env_api_key(cls) -> str:
        """The key the engine's environment variable holds; "" without
        one, or for an engine that reads none."""
        return os.environ.get(cls.env_key, "") if cls.env_key else ""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        request_sink: RequestSink | None = None,
        smooth: bool = False,
    ) -> None:
        self.config = config
        self._request_sink = request_sink
        self._smooth = smooth
        self._env_api_key = self.env_api_key()
        # Context sizes by model — only real answers are kept (see
        # `get_context_size`) — and whether their source raised this
        # session, which stops the asking.
        self._context_sizes: dict[str, int] = {}
        self._context_source_down = False

    @property
    def api_key(self) -> str:
        """The key in force: the section's, else the environment's, else
        "". What somebody typed beats what the shell carries, and a
        section's key cleared uncovers the variable's."""
        return self.config.api_key or self._env_api_key

    @property
    def key_source(self) -> KeySource | None:
        """Where `api_key` comes from — None for no key at all."""
        if self.config.api_key:
            return KeySource.SECTION
        if self._env_api_key:
            return KeySource.ENV
        return None

    @property
    def manages_models(self) -> bool:
        """Whether this provider loads and unloads models on demand —
        the picker offers the actions exactly then. Class knowledge for
        most engines; llama.cpp answers from its listing."""
        return False

    # ---------- the protocol ----------

    def models(self, timeout: float = ASK_TIMEOUT) -> list[ModelInfo]:
        """Every model this provider offers, as rich rows — the one
        listing call, one pass over each endpoint however long the list.
        Raises the error family when the server cannot answer. The base
        knows the plain /models ids, sorted, and of the models nothing."""
        data = http.get_json(
            f"{self.config.url}/models",
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
        )
        listed = data.get("data") if isinstance(data, dict) else None
        if not isinstance(listed, list):
            return []
        ids = sorted(str(m["id"]) for m in listed if isinstance(m, dict) and "id" in m)
        return [ModelInfo(name=name) for name in ids]

    def model(self, name: str, timeout: float = ASK_TIMEOUT) -> ModelInfo | None:
        """The row for one model — best effort: None when the provider
        does not offer it or cannot be reached. A listing is one pass,
        however long the list; this is the one-model ask, where an
        engine that keeps a model's facts behind a per-model call
        (Ollama's card) fills them in."""
        try:
            return self._model(name, timeout)
        except ProviderError:
            return None

    def get_context_size(self, model: str) -> int | None:
        """The loaded context size for `model`, or None when nobody can
        say. Only a real answer is cached — None is asked again, because
        the usual cause is asking before the model loads. A source that
        RAISES is a different case: a catalog that is down must not tax
        every turn with a timeout, so it stays unasked until a listing
        succeeds."""
        if model in self._context_sizes:
            return self._context_sizes[model]
        if self._context_source_down:
            return None
        try:
            size = self._context_size(model)
        except ProviderError:
            self._context_source_down = True
            return None
        if size:
            self._context_sizes[model] = size
        return size

    def complete_chat(
        self,
        model: str,
        messages: Sequence[WireMessage],
        params: dict[str, object],
        *,
        think_level: str | None = None,
        images: Sequence[Image] = (),
        timeout: float = REPLY_TIMEOUT,
        purpose: str = "chat",
        watched: bool = True,
        on_idle: Callable[[], None] | None = None,
    ) -> Iterator[Chunk]:
        """Stream one chat completion over the whole transcript: Thinking
        and Text deltas, then a final Stats. `think_level` is a
        `wire.THINKING_LEVELS` word, sent on every knob the engine reads and on
        none where it reads none; `images` ride on the last message.
        `watched=False` for a call nobody watches — an accumulated
        string gains nothing from pacing, and the held lag would only
        delay its cancel."""
        body = wire.chat_completion_body(
            model, messages, params, images=images, cache_ttl=self._cache_ttl()
        )
        knobs = wire.thinking_fields(think_level, self.thinking_knobs)
        url = f"{self.config.url}/chat/completions"
        stream = self._stream(url, body, knobs, purpose, timeout, wire.chat_delta)
        return self._paced(stream, watched, on_idle)

    def complete_text(
        self,
        model: str,
        prompt: str,
        params: dict[str, object],
        *,
        think_level: str | None = None,
        timeout: float = REPLY_TIMEOUT,
        purpose: str = "chat",
        watched: bool = True,
        on_idle: Callable[[], None] | None = None,
    ) -> Iterator[Chunk]:
        """Stream one text completion: the prompt continued exactly where
        it ends, Text deltas then a final Stats. `think_level` goes out
        on the engine's text knobs, and on none where it reads none
        there. No images: the prompt already IS the request. Whatever
        the model thinks arrives INLINE, inside the text — nothing
        stands between the prompt and the model to tell a thought from
        the rest."""
        body = wire.text_completion_body(model, prompt, params)
        knobs = wire.thinking_fields(think_level, self.text_thinking_knobs)
        url = f"{self.config.url}/completions"
        stream = self._stream(url, body, knobs, purpose, timeout, wire.completion_delta)
        return self._paced(stream, watched, on_idle)

    def count_chat_tokens(
        self, model: str, messages: Sequence[WireMessage], timeout: float = ASK_TIMEOUT
    ) -> int | None:
        """How many tokens the chat wire would spend on `messages`, as
        the engine itself counts them — None where it cannot say, which
        the base cannot. Best effort: a caller keeps its estimate for
        None."""
        return None

    def count_text_tokens(
        self, model: str, prompt: str, timeout: float = ASK_TIMEOUT
    ) -> int | None:
        """How many tokens the text wire would spend on `prompt` — None
        where the engine cannot say, which the base cannot."""
        return None

    def load_model(self, model: str) -> None:
        """Load on an engine that manages models; blocks until the server
        answers. Raises the error family — the base refuses, for an
        engine that does not."""
        raise ProviderError(f"Models cannot be loaded or unloaded on {self.config.name}.")

    def unload_model(self, model: str) -> None:
        raise ProviderError(f"Models cannot be loaded or unloaded on {self.config.name}.")

    def balance(self, timeout: float = ASK_TIMEOUT) -> Money | None:
        """The account balance as the provider reports it — None where
        there is no account or it will not say. Money, not a rendered
        string: what a reader sees is the frontends' to decide."""
        return None

    # ---------- the remaining hooks: each engine's native facts ----------

    def _model(self, name: str, timeout: float) -> ModelInfo | None:
        """One model's row: the listing's, for the base. An engine with
        a per-model call fills in what the listing could not."""
        return next((row for row in self.models(timeout) if row.name == name), None)

    def _decode_capabilities(self, listed: dict[str, Any]) -> Capabilities | None:
        """What the listing's own object for a model says it can do
        (`_full_models`); the base reads nothing off it."""
        return None

    def _context_size(self, model: str) -> int | None:
        """The engine's native way of asking the loaded context size; the
        base knows none. May raise: see `get_context_size`."""
        return None

    @property
    def _headers(self) -> dict[str, str]:
        """What every request to this provider carries: the protocol's
        bearer auth over the key in force, and whatever the engine's own
        service asks for on top (OpenRouter's attribution). The scheme
        belongs here and not to the section — a provider is configured
        with a key, never with the way a key is presented on the wire.
        The one door, so a header a subclass adds cannot miss a call
        site, and cannot reach another provider."""
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    # ---------- helpers for the engines ----------

    def _full_models(
        self, timeout: float, *, query: str = "", keyed: bool = False
    ) -> list[ModelInfo]:
        """`models` with everything the /models listing says beyond the
        ids: every model available (nothing to load, nothing to size),
        the context size where the listing carries `context_length` —
        not the protocol's, but the extension the catalogs share — and
        the capabilities the `_decode_capabilities` hook reads.
        `query` is what a service wants appended to include the details.
        `keyed` refuses first without a key that opens the account: a
        catalog can be public (OpenRouter's is), so a listing alone
        proves nothing, and rows from one would only invite a chat that
        fails with 401. The context sizes are seeded: the listing just
        paid for them, so a turn's ask never refetches what the picker
        brought home."""
        if keyed:
            if not self.api_key:
                raise UnauthorizedError(f"No api key for {self.config.name}.")
            if self.balance(timeout) is None:
                raise UnauthorizedError(f"The api key was rejected by {self.config.name}.")
        data = http.get_json(
            f"{self.config.url}/models{query}",
            name=self.config.name,
            headers=self._headers,
            timeout=timeout,
        )
        raw = data.get("data") if isinstance(data, dict) else None
        self._context_source_down = False
        rows = []
        for listed in raw if isinstance(raw, list) else []:
            if not isinstance(listed, dict) or not isinstance(listed.get("id"), str):
                continue
            name = str(listed["id"])
            context = wire.positive_int(listed.get("context_length"))
            if context:
                self._context_sizes[name] = context
            capabilities = self._decode_capabilities(listed)
            rows.append(ModelInfo(name, context=context, capabilities=capabilities, loaded=True))
        return sorted(rows, key=lambda row: row.name)

    # ---------- streaming ----------

    def _cache_ttl(self) -> str | None:
        """The prompt-cache TTL to mark with, or None: the engine must
        honour markers and the section must not have said off. Chat
        only — a marker is a content part of a message, and the text
        wire has no message to put one on."""
        if not self.cache_markers or self.config.prompt_cache == "off":
            return None
        return self.config.prompt_cache or "5m"

    def _stream(
        self,
        url: str,
        body: dict[str, object],
        knobs: dict[str, object],
        purpose: str,
        timeout: float,
        read_delta: _DeltaReader,
    ) -> Iterator[Chunk]:
        """The request with its thinking `knobs` on, and — should a 400
        refuse it before anything streams — once more without them.
        Engines differ on the knob: "off" cannot be sent to a model
        whose reasoning is mandatory, and some engines reject the field
        outright; the retry leaves them their own default. Only a take
        that produced NOTHING is retried: a mid-stream failure has words
        on someone's screen, and a second take would repeat them. No
        knobs, one take."""
        if not knobs:
            yield from self._generate(url, body, purpose, timeout, read_delta)
            return
        knobbed = self._generate(url, {**body, **knobs}, purpose, timeout, read_delta)
        yielded = False
        try:
            for chunk in knobbed:
                yielded = True
                yield chunk
        except GeneratorExit:
            # Deterministically, not at collection: cancel-and-keep
            # records the partial the moment the consumer lets go.
            knobbed.close()
            raise
        except StatusError as e:
            if yielded or e.status != 400:
                raise
            yield from self._generate(url, body, purpose, timeout, read_delta)

    def _paced(
        self, stream: Iterator[Chunk], watched: bool, on_idle: Callable[[], None] | None
    ) -> Iterator[Chunk]:
        if watched and self._smooth:
            return smoothing.smoothen(stream, on_idle)
        return stream

    def _generate(
        self,
        url: str,
        body: dict[str, object],
        purpose: str,
        timeout: float,
        read_delta: _DeltaReader,
    ) -> Generator[Chunk, None, None]:
        """One take: the request recorded, the wire read, and the answer
        filed under the request's id — once, however it ends: the clean
        end, the consumer closing it (the cancel-and-keep door), or a
        failure. What had arrived rides along either way."""
        request_id = ""
        if self._request_sink is not None:
            request_id = self._request_sink.record_request(self.config.name, purpose, body)
        start = time.monotonic()
        first_token_at: float | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        cached_tokens: int | None = None
        text: list[str] = []
        thoughts: list[str] = []
        recorded = False

        def answered(outcome: str) -> None:
            nonlocal recorded
            if recorded or self._request_sink is None or not request_id:
                return
            recorded = True
            waited = (first_token_at - start) if first_token_at is not None else None
            self._request_sink.record_answer(
                self.config.name,
                purpose,
                request_id,
                outcome=outcome,
                seconds=time.monotonic() - start,
                first_token_seconds=waited,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                text="".join(text),
                thinking="".join(thoughts),
            )

        events = http.stream_events(
            url, body, name=self.config.name, headers=self._headers, timeout=timeout
        )
        model = str(body["model"])
        try:
            trouble: list[str] = []
            for event in events:
                usage = wire.read_usage(event)
                if usage is not None:
                    prompt_tokens, completion_tokens, cached = usage
                    if cached is not None:
                        cached_tokens = cached
                if sentence := wire.trouble(event):
                    trouble.append(sentence)
                thinking, content = read_delta(event)
                if thinking:
                    first_token_at = first_token_at or time.monotonic()
                    thoughts.append(thinking)
                    yield Thinking(thinking)
                if content:
                    first_token_at = first_token_at or time.monotonic()
                    text.append(content)
                    yield Text(content)
            if trouble:
                raise DeclinedError("The model declined: " + "; ".join(trouble))
        except GeneratorExit:
            answered("cancelled")
            raise
        except Exception as e:
            answered(f"failed: {type(e).__name__}")
            raise
        finally:
            # Deterministically, not at collection: the connection closes
            # the moment the consumer lets go, and the server stops.
            events.close()

        end = time.monotonic()
        # Filed BEFORE the final yield: a consumer that takes the last
        # Text and closes without pulling the stats still leaves a
        # finished answer in the log, not a "cancelled".
        answered("ok")
        yield Stats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            context_max=self.get_context_size(model),
            duration_seconds=end - start,
            first_token_seconds=(first_token_at - start) if first_token_at is not None else None,
        )
