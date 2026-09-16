"""The completion half of every client: the protocol's two streams,
`/chat/completions` and `/completions`, the token counts where an
engine has them, and the class knowledge of the wire — the fields a
reasoning effort rides on, whether prompt-cache marks are honoured,
whether tokens are counted.

A stream yields `Reasoning` and `Text` deltas, then one `Stats`. A 400
to the reasoning knobs is retried once without them; a stream that
ends without `[DONE]` is a lost connection; closing a stream closes
the connection, which stops the server. Every request and its answer
are filed with the `RequestSink`, a stream's failure with the
transport, past the retry; bursty output is paced (`smoothing`) unless
nobody watches. The half reads the server off its config and asks it
through the transport it is handed; it knows nothing of the model half.
"""

import contextlib
import json
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, final

from otaku.providers import smoothing
from otaku.providers.errors import DeclinedError, ProviderError, StatusError, UnreachableError
from otaku.providers.http import ASK_TIMEOUT, REPLY_TIMEOUT, Cut, Http, StreamCut
from otaku.providers.openai import frames, reasoning, requests
from otaku.providers.openai.models import OpenAIModels
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
    # Why the model stopped, in the wire's word: "stop" for its own end
    # or a stop word, "length" for the reply limit — a reply cut short,
    # mid-sentence, that every engine reports the same way; None where
    # the stream ended without a word (cut, failed, or never said).
    finish_reason: str | None = None


Chunk = Text | Reasoning | Stats


class Reply(Iterator[Chunk]):
    """A reply as it streams: the chunks, and `stats`, filled as they
    come, for what the stream came to so far — a reader that stops
    reading (a cancel) still holds the wait it spent and any count the
    wire stated by then, to file. Closing it closes the stream."""

    def __init__(self, chunks: Iterator[Chunk], stats: Stats) -> None:
        self._chunks = chunks
        self.stats = stats

    def __iter__(self) -> Iterator[Chunk]:
        return self

    def __next__(self) -> Chunk:
        return next(self._chunks)

    def close(self) -> None:
        closer = getattr(self._chunks, "close", None)
        if closer is not None:
            closer()


class RequestSink(Protocol):
    """Where requests and their answers are recorded — the injected
    seam the session's request log satisfies, a mirror of
    `logging.RequestLog` method for method. `record_request` returns
    the id the answer is later filed under; `record_answer` files what
    the stream came to: the status, its `Stats`, the answer's text and
    reasoning."""

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


# The parameters every OpenAI endpoint reads, by the app's names — the
# ones `/set` takes (`backend.session.PARAMETERS`), which the body
# carries as they are.
PROTOCOL_PARAMS: frozenset[str] = frozenset(
    {"temperature", "top_p", "max_tokens", "presence_penalty", "frequency_penalty", "seed", "stop"}
)
# The three beyond the protocol; an engine declares the ones its
# endpoint reads. `top_k` and `min_p` are llama.cpp's names;
# `repetition_penalty` is omlx's and the catalogs' — llama.cpp and LM
# Studio read it as `repeat_penalty`, which their `_convert_params`
# respell.
SAMPLER_PARAMS: frozenset[str] = frozenset({"top_k", "min_p", "repetition_penalty"})
# The spellings a refusal of a sampler would name.
_SAMPLER_WORDS: frozenset[str] = SAMPLER_PARAMS | {"repeat_penalty"}

# A parameter's bounds as an engine holds them: (low, high), None for
# no bound on that side. The app's own table (`backend.session.PARAMETERS`)
# is the catalogs' — the protocol's 0 to 2 on temperature and the
# penalties; an engine that takes more, or less, says so here.
Bounds = tuple[float | None, float | None]


class OpenAICompletion:
    # ---------- class knowledge: what is true of the engine's wire ----------

    # The parameter bounds the engine holds where they differ from the
    # app's table: the local engines take any temperature and penalty,
    # NanoGPT no top_k below 1, KoboldCpp no repetition penalty below 1.
    bounds: ClassVar[dict[str, Bounds]] = {}
    # The parameters the wire reads, out of the app's — and the only
    # ones that go out (`_wire_params`): one outside the set is left
    # behind, since the engine would keep its default either way and a
    # strict server would refuse the field. A model is served by more
    # than one provider and its saved parameters are one set, so the
    # app hands every one over and the engine takes its own. The base
    # is the protocol's own, which every endpoint reads (Ollama's reads
    # nothing more); an engine adds what its server reads beyond it.
    # `_convert_params` respells, it never adds or drops.
    supported_params: ClassVar[frozenset[str]] = PROTOCOL_PARAMS

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
        http: Http,
        *,
        request_sink: RequestSink | None,
        smooth: bool,
        models: OpenAIModels | None = None,
    ) -> None:
        self._config = config
        self._http = http
        self._request_sink = request_sink
        self._smooth = smooth
        # The model half, for what a model honours of the parameters:
        # a catalog states it per route, and the wire sends only that.
        # Without one — a half on its own — the wire's set is the word.
        self._models = models

    # ---------- the protocol ----------

    @final
    def chat(
        self,
        model: str,
        messages: Sequence[WireMessage],
        params: dict[str, object],
        *,
        level: str | None = None,
        images: Sequence[Image] = (),
        timeout: float = REPLY_TIMEOUT,
        purpose: str = "chat",
        watched: bool = True,
        on_idle: Callable[[], bool | None] | None = None,
    ) -> Reply:
        """Stream one chat completion: Reasoning and Text deltas, then a
        final Stats. `level` is a thinking level in `reasoning`'s
        vocabulary — a rung, off or on, a budget — sent on every knob
        the engine reads; `images` ride on the last
        message; `watched=False` skips the pacing for a call nobody
        watches, so its cancel is not delayed. `on_idle` is ticked
        while the reply is waited on — before the first token above all
        — and may answer False to say nobody reads any more: the stream
        ends as cancelled then, its request cut. Given, it is ticked
        whether the reply is paced or not; without it, and without the
        pacing, the caller's own thread is in the read and nothing
        ticks."""
        body, knobs = self._chat_request(model, messages, params, level=level, images=images)
        url = f"{self._config.url}/chat/completions"
        return self._relayed(
            url, body, knobs, purpose, timeout, frames.chat_delta, watched, on_idle
        )

    @final
    def text(
        self,
        model: str,
        prompt: str,
        params: dict[str, object],
        *,
        level: str | None = None,
        timeout: float = REPLY_TIMEOUT,
        purpose: str = "chat",
        watched: bool = True,
        on_idle: Callable[[], bool | None] | None = None,
    ) -> Reply:
        """Stream one text completion: the prompt continued where it
        ends, Text deltas then a final Stats. `level` goes out on the
        engine's text knobs. Whatever the model reasons arrives
        inline, in the text — nothing stands between the prompt and
        the model to tell a thought from the rest."""
        body = requests.text_completion_body(model, prompt, self._wire_params(params, model))
        knobs = reasoning.fields(level, self.text_reasoning_knobs)
        url = f"{self._config.url}/completions"
        return self._relayed(
            url, body, knobs, purpose, timeout, frames.completion_delta, watched, on_idle
        )

    def _relayed(
        self,
        url: str,
        body: dict[str, object],
        knobs: dict[str, object],
        purpose: str,
        timeout: float,
        read_delta: _DeltaReader,
        watched: bool,
        on_idle: Callable[[], bool | None] | None,
    ) -> Reply:
        """The reply, read under a cut whenever a thread other than the
        reader's will hold the stream — the pacing's pump for a watched
        reply, the tick's for a caller with `on_idle` — and through the
        relay then: a pump thread reading, the caller's emitting and
        ticking `on_idle`, paced only for a reader watching. Without
        either, the caller's own thread reads and nobody could cut it."""
        stats = Stats()
        cut = Cut() if on_idle is not None or (watched and self._smooth) else None
        stream = self._stream(url, body, knobs, purpose, timeout, read_delta, stats, cut)
        if cut is None:
            return Reply(stream, stats)
        paced = watched and self._smooth
        return Reply(smoothing.smoothen(stream, on_idle, cut.cut, paced=paced), stats)

    def count_chat_tokens(
        self,
        model: str,
        messages: Sequence[WireMessage],
        *,
        level: str | None = None,
        images: Sequence[Image] = (),
        timeout: float = ASK_TIMEOUT,
    ) -> int | None:
        """How many tokens the chat wire would spend on `messages`, as
        the engine itself counts them, of the request a turn would send:
        `level` on the knobs and `images` on the last message, where
        the engine's count renders them. None where it cannot say, which
        the base cannot. Best level: a caller keeps its estimate for
        None."""
        return None

    def count_text_tokens(
        self, model: str, prompt: str, timeout: float = ASK_TIMEOUT
    ) -> int | None:
        """How many tokens the text wire would spend on `prompt` — None
        where the engine cannot say, which the base cannot."""
        return None

    # ---------- streaming ----------

    def honoured(self, model: str) -> frozenset[str]:
        """The app's parameters that reach `model`: the ones this wire
        reads (`supported_params`), and among those, where a catalog
        states the model's own (`ModelCapabilities.supported_params`),
        the ones it honours — the rest it would drop, silently. Off the
        cache, as listed; the wire's whole set for a model not listed."""
        found = self._models.cached(model) if self._models is not None else None
        stated = found.capabilities.supported_params if found and found.capabilities else None
        return self.supported_params if stated is None else self.supported_params & stated

    def _wire_params(self, params: dict[str, object], model: str) -> dict[str, object]:
        """The params as they go out: the ones that reach `model`
        (`honoured`), spelled the engine's way (`_convert_params`)."""
        reaching = self.honoured(model)
        return self._convert_params({k: v for k, v in params.items() if k in reaching})

    def _chat_request(
        self,
        model: str,
        messages: Sequence[WireMessage],
        params: dict[str, object],
        *,
        level: str | None,
        images: Sequence[Image],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """The chat request as the wire gets it: the body, and the
        reasoning knobs beside it — apart, since a 400 to the knobs
        sends the body again without them. What a turn sends, and what
        a count is asked to count."""
        body = requests.chat_completion_body(
            model,
            messages,
            self._wire_params(params, model),
            images=images,
            cache_ttl=self._cache_ttl(),
        )
        return body, reasoning.fields(level, self.chat_reasoning_knobs)

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
        stats: Stats,
        cut: Cut | None = None,
    ) -> Iterator[Chunk]:
        """The request with its reasoning `knobs`, and — should a 400
        refuse it before anything streamed, naming a knob — once more
        without them: "none" cannot be sent to a model whose reasoning
        is mandatory, and some engines reject the field outright. The
        second take is filed as such, with the refusal's words: sent
        bare, the engine keeps its default, which for a model that
        insists on reasoning is reasoning — visible on screen as the
        thought it streams, and in the log as this note. A 400 that
        names no knob (a context overflow) is the answer, sent once; a
        take that yielded is never retried, since its words are on
        someone's screen. The same once more for a sampler: a strict
        server (OpenAI's own, behind the generic provider) refuses a
        field it does not know by name, and the take goes out again
        without the samplers. Nothing named, one take. A failure is
        filed as it escapes — past the retry, so a 400 that was sent
        again was no failure."""
        try:
            first = self._generate(url, {**body, **knobs}, purpose, timeout, read_delta, stats, cut)
            yielded = False
            try:
                # Closed the moment the consumer lets go, not at collection:
                # cancel-and-keep records the partial right then.
                with contextlib.closing(first):
                    for chunk in first:
                        yielded = True
                        yield chunk
            except StatusError as e:
                # The server's whole sentence, not the cut a reader is
                # shown: the word can sit past the cut. Less the model's
                # own name: an id may spell "thinking" (NanoGPT's
                # `:thinking` models) and a 400 that echoes it — a context
                # overflow — names no knob.
                sentence = (e.detail or str(e)).lower()
                sentence = sentence.replace(str(body.get("model", "")).lower(), "")
                knob_refused = bool(knobs) and any(
                    word in sentence for word in self._refusal_words(knobs)
                )
                # A strict server (OpenAI's own, behind the generic
                # provider) refuses a sampler it does not know by name:
                # sent again without the samplers, the knobs kept.
                samplers = {k for k in body if k in _SAMPLER_WORDS}
                sampler_refused = bool(samplers) and any(word in sentence for word in samplers)
                if yielded or e.status != 400 or not (knob_refused or sampler_refused):
                    raise
                again = (
                    {k: v for k, v in body.items() if k not in samplers}
                    if sampler_refused
                    else body
                )
                again_knobs = {} if knob_refused else knobs
                dropped = " and ".join(
                    what
                    for what, went in (
                        ("the reasoning knobs", knob_refused),
                        ("the samplers", sampler_refused),
                    )
                    if went
                )
                yield from self._generate(
                    url,
                    {**again, **again_knobs},
                    purpose,
                    timeout,
                    read_delta,
                    stats,
                    cut,
                    retried=f"sent again without {dropped}: {e}",
                )
        except ProviderError as e:
            self._http.record(e, purpose)
            raise

    def _generate(
        self,
        url: str,
        body: dict[str, object],
        purpose: str,
        timeout: float,
        read_delta: _DeltaReader,
        stats: Stats,
        cut: Cut | None = None,
        retried: str = "",
    ) -> Generator[Chunk, None, None]:
        """One take: the request recorded, the wire read, and the answer
        filed under the request's id — once, however it ends: the clean
        end, the consumer closing it (the cancel-and-keep door), a cut
        it asked for from another thread, or a failure. What had
        arrived rides along either way, in `stats` — the caller's, filled
        as the stream goes and yielded whole at a clean end, so a caller
        that stopped reading still holds what came. `retried` is the
        note a second take is filed with — what was dropped, and the
        refusal that made it — so the log says why."""
        name = self._config.name
        request_id = ""
        if self._request_sink is not None:
            request_id = self._request_sink.record_request(name, purpose, body)
        start = time.monotonic()
        text: list[str] = []
        thoughts: list[str] = []
        trouble: list[str] = []
        lines = self._http.stream(url, body, timeout=timeout, cut=cut)
        events = self._events(lines, name)
        # A close before the end is a cancel: GeneratorExit is no
        # Exception, so it leaves the word as it is.
        status = "cancelled"
        try:
            try:
                for event in events:
                    usage = self._read_usage(event)
                    if usage is not None:
                        # Each count as the report states it: a later
                        # report that states one alone leaves the others.
                        prompt, completion, cached = usage
                        if prompt is not None:
                            stats.prompt_tokens = prompt
                        if completion is not None:
                            stats.completion_tokens = completion
                        if cached is not None:
                            stats.cached_tokens = cached
                    sentence = frames.trouble(event)
                    if sentence and sentence not in trouble:
                        trouble.append(sentence)
                    reason = frames.finish(event)
                    if reason:
                        stats.finish_reason = reason
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
                # An engine that reports its trouble mid-stream (llama.cpp)
                # ends without [DONE]: the trouble is the answer, not the
                # lost connection that follows it.
                if not trouble:
                    raise
            if trouble:
                raise DeclinedError(" ".join(trouble))
            status = "ok" if not retried else f"ok, {retried}"
        except StreamCut:
            return  # the consumer cut the read: cancelled, as the word stands
        except Exception as e:
            # The provider's sentence where there is one; a bug's type name.
            status = f"failed: {e if isinstance(e, ProviderError) else type(e).__name__}"
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
        that will not parse — is skipped, except a bare JSON object
        carrying `error`: a server that answers a wrong path with a 200
        and one of those (LM Studio's "Unexpected endpoint") sent a frame
        in all but name, and its sentence beats "lost the connection".
        Lines that run out without `[DONE]` are a lost connection: every
        engine sends it on a clean end, and Ollama closes without it
        after swallowing a runner's error mid-reply."""
        for line in lines:
            if not line.startswith("data:"):
                event = _object(line)
                if event is not None and "error" in event:
                    yield event
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                return
            event = _object(payload)
            if event is not None:
                yield event
        raise UnreachableError(f"Lost the connection to {name}.")

    # ---------- the hooks ----------

    def _convert_params(self, params: dict[str, object]) -> dict[str, object]:
        """The params as this engine spells them: the app sends
        `repetition_penalty`, which llama.cpp and LM Studio read only as
        `repeat_penalty`. A respelling only — what the engine reads at
        all is `supported_params`. The base spells nothing differently."""
        return params

    def _read_usage(self, event: dict[str, Any]) -> frames.Usage | None:
        """The usage report on a frame, or None: the protocol's `usage`
        object, which is all the base reads."""
        return frames.read_usage(event)


def _object(text: str) -> dict[str, Any] | None:
    """`text` as the JSON object it is, or None: not JSON, or not an object."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
