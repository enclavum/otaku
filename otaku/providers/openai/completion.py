"""The completion half of every client: the protocol's two streams,
`/chat/completions` and `/completions`, the token counts where an
engine has them, and the class knowledge of the wire — the fields a
reasoning effort rides on, whether prompt-cache marks are honoured,
whether tokens are counted.

A stream yields `Reasoning` and `Text` deltas, then one `Stats`. A 400
to the reasoning knobs is retried once without them; a stream that
ends without `[DONE]` is a lost connection; closing a stream closes
the connection, which stops the server. Every request and its answer
are filed with the `RequestSink`; bursty output is paced (`smoothing`)
unless nobody watches. The half reads the server off its config and
the headers off its auth; it knows nothing of the model half.
"""

import contextlib
import json
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, final

from otaku.providers import http, smoothing
from otaku.providers.errors import DeclinedError, ProviderError, StatusError, UnreachableError
from otaku.providers.http import ASK_TIMEOUT, REPLY_TIMEOUT
from otaku.providers.openai import frames, reasoning, requests
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.requests import Image, WireMessage
from otaku.settings.providers import ProviderConfig

# What a stream reads off each frame: (reasoning, text) deltas.
_DeltaReader = Callable[[dict[str, Any]], tuple[str, str]]


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Reasoning:
    text: str


@dataclass
class Stats:
    """What one stream came to, filled as it goes: the usage report's
    tokens, and the two spans that split a turn — the wait for the
    first token (the prefill) and the rest (the generation, whose
    tok/s is completion_tokens over the difference)."""

    prompt_tokens: int | None = None
    # Generated, reasoning included where the engine counts it so — the
    # wire's own word for them.
    completion_tokens: int | None = None
    # Of prompt_tokens, served from the provider's cache; None where the
    # provider reports nothing.
    cached_tokens: int | None = None
    first_token_seconds: float | None = None  # request sent → first token; None when none came
    total_seconds: float = 0.0  # request sent → stream ended


Chunk = Text | Reasoning | Stats


class RequestSink(Protocol):
    """Where requests and their answers are recorded — the injected
    seam the session's request log satisfies. `record_request` returns
    the id the answer is later filed under; `record_answer` files what
    the stream came to: the status, its `Stats`, the answer's text
    and reasoning."""

    def record_request(self, provider: str, purpose: str, body: dict[str, object]) -> str: ...

    def record_answer(
        self,
        provider: str,
        purpose: str,
        request_id: str,
        *,
        status: str,
        stats: Stats,
        text: str,
        reasoning: str = "",
    ) -> None: ...


class OpenAICompletion:
    # ---------- class knowledge: what is true of the engine's wire ----------

    # Honours prompt-cache breakpoints (`cache_control` on content
    # parts: Anthropic's marking, forwarded by OpenRouter). The
    # section's `prompt_cache` key ("off" | "5m" | "1h") modulates it,
    # never enables it where the engine cannot.
    can_mark_cache: ClassVar[bool] = False
    # The request fields a reasoning effort goes out on: the protocol's
    # `reasoning_effort`, the chat template's flag, or both; none for
    # an engine that reads neither.
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset({reasoning.EFFORT_KNOB})
    # The same for the text wire, where no template stands between the
    # prompt and the model; the base sends nothing there.
    text_reasoning_knobs: ClassVar[frozenset[str]] = frozenset()
    # Whether `count_chat_tokens` and `count_text_tokens` answer — the
    # engine's fact, not a model's.
    can_count_tokens: ClassVar[bool] = False

    def __init__(
        self,
        config: ProviderConfig,
        auth: OpenAIAuth,
        *,
        request_sink: RequestSink | None,
        smooth: bool,
    ) -> None:
        self._config = config
        self._auth = auth
        self._request_sink = request_sink
        self._smooth = smooth

    # ---------- the protocol ----------

    @final
    def chat(
        self,
        model: str,
        messages: Sequence[WireMessage],
        params: dict[str, object],
        *,
        effort: str | None = None,
        images: Sequence[Image] = (),
        timeout: float = REPLY_TIMEOUT,
        purpose: str = "chat",
        watched: bool = True,
        on_idle: Callable[[], None] | None = None,
    ) -> Iterator[Chunk]:
        """Stream one chat completion: Reasoning and Text deltas, then a
        final Stats. `effort` is a `reasoning.EFFORTS` word, sent on
        every knob the engine reads; `images` ride on the last
        message; `watched=False` skips the pacing for a call nobody
        watches, so its cancel is not delayed."""
        body = requests.chat_completion_body(
            model,
            messages,
            self._convert_params(params),
            images=images,
            cache_ttl=self._cache_ttl(),
        )
        knobs = reasoning.fields(effort, self.chat_reasoning_knobs)
        url = f"{self._config.url}/chat/completions"
        stream = self._stream(url, body, knobs, purpose, timeout, frames.chat_delta)
        return smoothing.smoothen(stream, on_idle) if watched and self._smooth else stream

    @final
    def text(
        self,
        model: str,
        prompt: str,
        params: dict[str, object],
        *,
        effort: str | None = None,
        timeout: float = REPLY_TIMEOUT,
        purpose: str = "chat",
        watched: bool = True,
        on_idle: Callable[[], None] | None = None,
    ) -> Iterator[Chunk]:
        """Stream one text completion: the prompt continued where it
        ends, Text deltas then a final Stats. `effort` goes out on the
        engine's text knobs. Whatever the model reasons arrives
        inline, in the text — nothing stands between the prompt and
        the model to tell a thought from the rest."""
        body = requests.text_completion_body(model, prompt, self._convert_params(params))
        knobs = reasoning.fields(effort, self.text_reasoning_knobs)
        url = f"{self._config.url}/completions"
        stream = self._stream(url, body, knobs, purpose, timeout, frames.completion_delta)
        return smoothing.smoothen(stream, on_idle) if watched and self._smooth else stream

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

    # ---------- streaming ----------

    def _cache_ttl(self) -> str | None:
        """The prompt-cache TTL to mark with, or None: the engine must
        honour markers and the section must not have said off. Chat
        only — a marker is a content part of a message, and the text
        wire has no message to put one on."""
        if not self.can_mark_cache or self._config.prompt_cache == "off":
            return None
        return self._config.prompt_cache or "5m"

    def _stream(
        self,
        url: str,
        body: dict[str, object],
        knobs: dict[str, object],
        purpose: str,
        timeout: float,
        read_delta: _DeltaReader,
    ) -> Iterator[Chunk]:
        """The request with its reasoning `knobs`, and — should a 400
        refuse it before anything streamed, naming a knob — once more
        without them: "none" cannot be sent to a model whose reasoning
        is mandatory, and some engines reject the field outright. A 400
        that names no knob (a context overflow) is the answer, sent
        once; a take that yielded is never retried, since its words are
        on someone's screen. No knobs, one take."""
        if not knobs:
            yield from self._generate(url, body, purpose, timeout, read_delta)
            return
        knobbed = self._generate(url, {**body, **knobs}, purpose, timeout, read_delta)
        yielded = False
        try:
            # Closed the moment the consumer lets go, not at collection:
            # cancel-and-keep records the partial right then.
            with contextlib.closing(knobbed):
                for chunk in knobbed:
                    yielded = True
                    yield chunk
        except StatusError as e:
            knob_refused = any(word in str(e).lower() for word in self._refusal_words(knobs))
            if yielded or e.status != 400 or not knob_refused:
                raise
            yield from self._generate(url, body, purpose, timeout, read_delta)

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
        name = self._config.name
        request_id = ""
        if self._request_sink is not None:
            request_id = self._request_sink.record_request(name, purpose, body)
        start = time.monotonic()
        stats = Stats()
        text: list[str] = []
        thoughts: list[str] = []
        trouble: list[str] = []
        lines = http.stream_lines(url, body, name=name, headers=self._auth.headers, timeout=timeout)
        events = self._events(lines, name)
        # A close before the end is a cancel: GeneratorExit is no
        # Exception, so it leaves the word as it is.
        status = "cancelled"
        try:
            try:
                for event in events:
                    usage = self._read_usage(event)
                    if usage is not None:
                        stats.prompt_tokens, stats.completion_tokens, cached = usage
                        if cached is not None:
                            stats.cached_tokens = cached
                    sentence = frames.trouble(event)
                    if sentence and sentence not in trouble:
                        trouble.append(sentence)
                    thought, content = read_delta(event)
                    if (thought or content) and stats.first_token_seconds is None:
                        stats.first_token_seconds = time.monotonic() - start
                    if thought:
                        thoughts.append(thought)
                        yield Reasoning(thought)
                    if content:
                        text.append(content)
                        yield Text(content)
            except UnreachableError:
                # An engine that reports a refusal mid-stream (llama.cpp)
                # ends without [DONE]: the refusal is the answer, not
                # the lost connection that follows it.
                if not trouble:
                    raise
            if trouble:
                raise DeclinedError("The model declined: " + "".join(trouble))
            status = "ok"
        except Exception as e:
            status = f"failed: {type(e).__name__}"
            raise
        finally:
            # Deterministically, not at collection: the connection closes
            # the moment the consumer lets go, and the server stops. A
            # close that fails is no reason to leave the answer unfiled.
            with contextlib.suppress(ProviderError):
                lines.close()
            stats.total_seconds = time.monotonic() - start
            if self._request_sink is not None and request_id:
                self._request_sink.record_answer(
                    name,
                    purpose,
                    request_id,
                    status=status,
                    stats=stats,
                    text="".join(text),
                    reasoning="".join(thoughts),
                )
        yield stats

    @staticmethod
    def _refusal_words(knobs: dict[str, object]) -> set[str]:
        """The field names a refusal of `knobs` would mention: each key,
        nested ones included, and the stems a sentence uses without the
        field — OpenRouter's "reasoning" for a mandatory one, Ollama's
        "thinking" for a model without any."""
        words = {"reasoning", "thinking"}
        for key, value in knobs.items():
            words.add(key)
            if isinstance(value, dict):
                words.update(value)
        return words

    @staticmethod
    def _events(lines: Iterator[str], name: str) -> Generator[dict[str, Any], None, None]:
        """The protocol's frames off a stream's lines: each `data:` line's
        JSON, up to `[DONE]`; anything else — comments, keepalives, a line
        that will not parse — is skipped. Lines that run out without
        `[DONE]` are a lost connection: every engine sends it on a clean
        end, and Ollama closes without it after swallowing a runner's error
        mid-reply."""
        for line in lines:
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                return
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event
        raise UnreachableError(f"Lost the connection to {name}.")

    # ---------- the hooks ----------

    def _convert_params(self, params: dict[str, object]) -> dict[str, object]:
        """The sampling params as this engine spells them: the app sends
        `repetition_penalty`, which llama.cpp and LM Studio read only as
        `repeat_penalty`. The base spells nothing differently."""
        return params

    def _read_usage(self, event: dict[str, Any]) -> frames.Usage | None:
        """The usage report on a frame, or None: the protocol's `usage`
        object, which is all the base reads."""
        return frames.read_usage(event)
