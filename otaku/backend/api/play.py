"""Playing the story: one submitted line, the reply's event stream, and
the takes over it (regenerate, undo).

The stream is the frontend's to drive: iterate to render, close to
cancel. The event vocabulary lives here, `Text` and `Reasoning` included
(re-exported from providers — members of this module's union) and
`ToolCall` (`context.tool_calls`': a piece of a call, the tool's name
and its inside, never a fence — the deltas are fed through a
`ReplyParser`, so neither frontend parses). What is RECORDED is
everything that arrived, fences included; a call of a tool the user
answers ends the reply, and nothing past it is read. Closing
mid-stream keeps and records what arrived — Ctrl+C and Ctrl+R are the
frontend closing the generator; a Ctrl+R then simply calls `regenerate`
for the fresh take. Exactly one of Declined, Failed, or Done ends a
stream. A new reply arms the worker's idle-debounced extraction pass;
every submission (submit, regenerate, undo) defers pending work first.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from otaku.backend.api.cards import drop_unplayed_card
from otaku.backend.api.lore import build_job
from otaku.backend.files import (
    CANNOT_SEE,
    MAX_PICTURES,
    TOO_MANY,
    Picture,
    RawFile,
    read_picture,
    save,
)
from otaku.backend.session import NO_MODEL_HINT, Refused, Session
from otaku.backend.story import StorySettings, ToolSetting
from otaku.backend.tools import TOOLS, Actor, read
from otaku.context import syntax, tool_calls
from otaku.context.assembler import ContextOverflowError
from otaku.context.tool_calls import FENCE, Prose, ReplyParser
from otaku.context.tool_calls import ToolCall as ToolCall
from otaku.formatting import format_context
from otaku.providers import ProviderError, Stats
from otaku.providers import Reasoning as Reasoning
from otaku.providers import Text as Text
from otaku.store.schema import Character, Message


@dataclass(frozen=True)
class Recorded:
    """The user's turn landed (autocorrect settled first) — echo it.
    `note` is the record's own line to show dim beside the echo — the
    dice a /roll rolled; "" for every other turn."""

    message: Message
    note: str = ""


@dataclass(frozen=True)
class Declined:
    """The stream declines mid-way — it ends here; what already happened
    (a Recorded turn) stands. `reason` is the sentence to show. Named
    apart from the `Refused` exception on purpose: an exception is
    raised eagerly BEFORE the iterator exists, this event arrives inside
    one."""

    reason: str


@dataclass(frozen=True)
class Failed:
    """The stream failed; what already streamed is kept and recorded.
    `reason` is the sentence to show."""

    reason: str


@dataclass(frozen=True)
class ReplyReport:
    """What the reply came to: the stream's own account (`stats` — the
    spans, the counts, why the model stopped) beside what the stream
    cannot know, the context the prompt was measured against; and what
    follows from both. A frontend draws its stats line from these;
    `text()` is the line the terminal prints. `truncated` is a reply
    the model did not finish — cut at the reply limit, mid-sentence —
    and `notice` the sentence that says so, "" otherwise."""

    stats: Stats
    max_context: int | None

    @property
    def truncated(self) -> bool:
        return self.stats.finish_reason == "length"

    @property
    def notice(self) -> str:
        return "The reply was cut short at the max_tokens limit." if self.truncated else ""

    @property
    def rate(self) -> float | None:
        """Tokens per second over the generation alone — the span after
        the first token; None without a count or a span."""
        stats = self.stats
        if stats.completion_tokens is None or stats.first_token_seconds is None:
            return None
        span = stats.total_seconds - stats.first_token_seconds
        return stats.completion_tokens / span if span > 0 else None

    @property
    def context_used(self) -> int | None:
        """Percent of the context the prompt took; None where either
        figure is unknown."""
        if self.stats.prompt_tokens is None or not self.max_context:
            return None
        return round(100 * self.stats.prompt_tokens / self.max_context)

    def text(self) -> str:
        """The verbose stats line:

            [ total 1.3s, prompt 40 tok, eval 37 tok @ 35.2 tok/s, ctx 12% / 32K ]

        `total` is wall-clock for the whole request; the rate is over
        the decode-only span, so it reflects generation speed. Fields
        with no underlying value are skipped."""
        stats = self.stats
        parts: list[str] = [f"total {stats.total_seconds:.1f}s"]
        if stats.prompt_tokens is not None:
            parts.append(f"prompt {stats.prompt_tokens} tok")
        if stats.cached_tokens is not None:
            # Zero included: "cached 0 tok" is how a reader discovers their
            # pacing outlives the cache TTL (see providers.toml prompt_cache).
            parts.append(f"cached {stats.cached_tokens} tok")
        if stats.completion_tokens is not None:
            rate = self.rate
            parts.append(
                f"eval {stats.completion_tokens} tok"
                + (f" @ {rate:.1f} tok/s" if rate is not None else "")
            )
        if self.context_used is not None:
            parts.append(f"ctx {self.context_used}% / {format_context(self.max_context)}")
        return f"[ {', '.join(parts)} ]"


@dataclass(frozen=True)
class Done:
    """The turn's end: the recorded reply (None when nothing arrived),
    what it came to (None when the stream said nothing of itself), and
    the verbose stats line — the report's `text()`, "" unless /set
    verbose, so a frontend prints it as it is. A reply that ended on a
    question is in the reply's own segments, for a frontend to pose:
    the next line played is the answer, and nothing more is built
    around answering."""

    reply: Message | None
    report: ReplyReport | None
    stats: str


PlayEvent = Recorded | Text | Reasoning | ToolCall | Declined | Failed | Done


def submit(session: Session, line: str, files: Sequence[RawFile] = ()) -> Iterator[PlayEvent]:
    """One submitted story line (prose or a direction — never a command;
    the frontend routed those already), with `files` the pictures the
    reader attached to it — a line may be empty when it carries one.
    Validation is EAGER: invalid syntax raises Refused (the usage line)
    before the iterator is returned and before anything is recorded —
    the body is a plain function that validates and returns the inner
    generator, never a generator itself — and so does every picture:
    made ready here, refused here (the model cannot see, too many, a
    file that is no picture), stored only when the turn records, so a
    refusal leaves the story and the folder untouched. Then: the typed
    name settles to the cast's
    spelling, the turn records, Recorded is yielded, and the reply
    streams as Reasoning/Text deltas, Failed on a stream error, Done at
    the end. With no model selected the turn is still recorded — it is
    story — and Declined(NO_MODEL_HINT) ends the stream after Recorded.
    Closing the generator mid-stream records the partial reply and its
    usage. A new reply arms the worker's idle extraction pass —
    [lore_extraction].enabled gates ONLY this automatic arming; the
    forced /extract path always works."""
    frame = syntax.read(line)
    error = frame.check()
    if error is not None:
        raise Refused(error)
    # Every picture read here, eagerly like the syntax: a refusal leaves
    # the story and the folder untouched, and nothing is saved before
    # the turn records.
    if files and not session.vision:
        raise Refused(CANNOT_SEE)
    if len(files) > MAX_PICTURES:
        raise Refused(TOO_MANY)
    return _submit_events(session, frame, line, [read_picture(file) for file in files])


def regenerate(session: Session) -> Iterator[PlayEvent]:
    """Re-run the last prompt: the standing reply becomes a sibling and
    the fresh take streams (Text/Reasoning/Failed/Done — no Recorded; the
    prompt is already on screen or in `session.messages`). The dropped
    reply's kind and speaker are re-derived from the prompt, exactly as
    when it first played. Raises Refused when there is no model or
    nothing to regenerate — EAGERLY, before the iterator is returned and
    before anything is dropped (the same validate-then-return-generator
    shape as `submit`)."""
    if session._client() is None:
        # Before anything is dropped: without a model there is no fresh
        # take, and the standing reply must survive untouched.
        raise Refused(NO_MODEL_HINT)
    if not session.messages:
        raise Refused("Nothing to regenerate.")
    return _regenerate_events(session)


def segments(body: str) -> list[dict[str, object]]:
    """A stored body split for a frontend that parses nothing: prose
    (`{"kind": "prose", "text"}`) and each tool call as its tool's facts
    (`{"kind": "tool_call", …}` — a name no tool owns is a call all the
    same, with its tool and text alone), in order."""
    out: list[dict[str, object]] = []
    for part in tool_calls.parse_reply(body):
        if isinstance(part, Prose):
            out.append({"kind": "prose", "text": part.text})
        elif part.name in TOOLS:
            out.append({"kind": "tool_call", **read(part.name, part.text).to_json()})
        else:
            out.append({"kind": "tool_call", "tool": part.name, "text": part.text})
    return out


def undo(session: Session) -> list[Message]:
    """Discard the trailing exchange; returns the popped messages (empty
    = nothing to undo). Nothing is deleted — the head moves back. An
    unplayed imported character is dropped with their card exchange, so
    a retried import is never refused as a duplicate."""
    session.defer()
    popped = session._undo()
    if popped:
        drop_unplayed_card(session, popped)
    return popped


# ---------- the pictures on a turn ----------


def picture(session: Session, name: str) -> tuple[bytes, str] | None:
    """A stored picture as the model saw it, with its media type, under
    the name a turn's attachments carry — for a page that
    draws the transcript. None when the folder has no such file."""
    return session._store.files.get(name)


def thumbnail(session: Session, name: str) -> bytes | None:
    """Its thumbnail, a JPEG, under the same rule."""
    return session._store.files.get_thumb(name)


# ---------- the stream internals ----------


def _submit_events(
    session: Session, frame: syntax.Line, line: str, pictures: Sequence[Picture]
) -> Iterator[PlayEvent]:
    session.defer()
    # The named character, resolved once and used three ways: the
    # autocorrect rewrite, the request's speaker (/me — the line IS their
    # words), and the reply's (/you — it asks them to answer). Safe to
    # act on because `characters.find` matches an exact name or alias
    # only: it can settle a spelling or attribute a line, never pick
    # someone else. The name guard is the hot path's: a prose line names
    # nobody and must not query the cast for it.
    known = (
        session._store.characters.find(session._story_id, frame.name)
        if frame.name and session._story_id is not None
        else None
    )
    if known is not None and session.autocorrect:
        # Settled BEFORE anything sees it, so the echo, the store, and
        # the wire all read one text — a played line never moves after.
        line = frame.update_name(known.name)
    request_speaker = known if frame.speaks == "request" else None
    # The pictures go into the folder before the turn records, under
    # the story's number — so a story that does not exist yet is made
    # here, a step before the turn would have made it. Nothing was
    # written before this point.
    attachments = tuple(
        save(session._store.files, picture, session._ensure_story()) for picture in pictures
    )
    session._record_turn(
        Message(
            role="user",
            body=line,
            kind=frame.request_kind,
            # Resolved at RECORD time — the template is data on the turn,
            # filled with the line's own name and text only at wire time.
            # `freeze_template` bakes in what must never re-compose: a
            # /roll's dice are decided here, once, and never again.
            template=frame.freeze_template(
                getattr(session._prompts, frame.template_field) if frame.template_field else None
            ),
            speaker=request_speaker.name if request_speaker else None,
            speaker_id=request_speaker.id if request_speaker else None,
            attachments=attachments,
        )
    )
    yield Recorded(session._messages[-1], note=frame.note)
    reply_speaker = known if frame.speaks == "reply" else None
    yield from _reply_events(session, reply_kind=frame.reply_kind, reply_speaker=reply_speaker)


def _regenerate_events(session: Session) -> Iterator[PlayEvent]:
    session.defer()
    popped = session._drop_last_reply()
    # The prompt the take re-runs — absent when the story is a promptless
    # reply, which regenerates on its own.
    prompt = session._messages[-1] if session._messages else None
    # The prompt decides what its answer is, exactly as when it first
    # played — the kind AND the speaker — and it decides even for a
    # replaced reply, so an edit is honoured. Only a promptless reply has
    # nothing to ask, and then the reply it replaces says what it was —
    # its kind, not its speaker: that was extraction's read of the text
    # this take replaces, and the fresh text earns its own.
    speaker = None
    if prompt is not None:
        frame = syntax.read(prompt.body)
        kind = frame.reply_kind
        if frame.speaks == "reply" and session._story_id is not None:
            speaker = session._store.characters.find(session._story_id, frame.name)
    else:
        kind = popped.kind if popped is not None else "dialogue"
    yield from _reply_events(session, reply_kind=kind, reply_speaker=speaker)


def _reply_events(
    session: Session, *, reply_kind: str, reply_speaker: Character | None
) -> Iterator[PlayEvent]:
    """Stream one completion for the current transcript, yielding deltas,
    and record whatever arrived — on a clean end, a failure, and a
    mid-stream close alike. `reply_kind` is the kind the REPLY is stored
    under — never inferred from the prompt's own kind, because a /you
    switch also ends on an ((OOC:)) turn yet wants an in-character
    answer. `reply_speaker` is the cast member the reply is spoken by,
    when the prompt named one (/you): stored on the reply row, where
    extraction's own labeling is fill-only, so it stands."""
    client = session._client()
    if client is None:
        # The turn is recorded — it is story — and plays on a regenerate
        # once a model exists.
        yield Declined(NO_MODEL_HINT)
        return
    try:
        # A cold model is loaded first, so the window the prompt is cut
        # to is the runner's and not a substitute (`OpenAIModels.ready`).
        found = client.models.ready(session.model, on_idle=session._on_idle)
        max_context = found.max_context if found else None
        wire = session.assemble(max_context).messages
    except ProviderError as e:
        # The load the turn needed did not happen: the turn is recorded
        # and plays once the engine can, on a regenerate.
        yield Declined(str(e))
        return
    except ContextOverflowError as e:
        # The turn is recorded — it is story — and plays once the limit
        # is raised or more scenes close. The sentence is the assembler's.
        yield Declined(str(e))
        return
    content: list[str] = []
    held = ""  # a whitespace run the stream has not yet earned sending
    final: Stats | None = None
    error: str | None = None
    # The tool calls told apart from the prose as they stream. A call of
    # a tool the user answers ends the reply where its closing fence
    # does: the stream is cut there, and nothing rides the request for
    # it — the rule is otaku's, the same on every engine.
    parser = ReplyParser(ending=_ending_switched_on(session))
    stream = client.completion.chat(
        session.model,
        wire,
        dict(session.params),
        level=session.think,
        purpose="chat",
        # The thread this runs on belongs to the frontend between
        # tokens, if the frontend said what to do with it.
        on_idle=session._on_idle,
    )
    try:
        try:
            for chunk in stream:
                if isinstance(chunk, Reasoning):
                    yield chunk
                elif isinstance(chunk, Text):
                    # Some models pad the reply with blank lines. The text
                    # starts at its first real character, and a trailing
                    # whitespace run is held back — sent only when more
                    # text follows it, dropped at the stream's end. What
                    # is recorded is exactly what was yielded.
                    text = held + chunk.text
                    held = ""
                    if not content:
                        text = text.lstrip()
                    stripped = text.rstrip()
                    held, text = text[len(stripped) :], stripped
                    if not text:
                        continue
                    content.append(text)
                    yield from _pieces(parser.feed(text))
                    if parser.cut is not None:
                        break
                elif isinstance(chunk, Stats):
                    final = chunk
            if parser.cut is None:
                yield from _pieces(parser.flush())
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
    except (GeneratorExit, KeyboardInterrupt):
        # The frontend closed the stream mid-way, or the terminal's
        # Ctrl+C landed in the wait itself, inside this frame — the same
        # cancel-and-keep. No more yields are possible: record and
        # re-raise. What the stream came to so far is filed too — the
        # prefill was spent and billed, whatever the reader saw.
        _land_reply(session, content, stream.stats, reply_kind, reply_speaker)
        raise
    except Exception as e:  # the stream failed; what streamed is kept
        # The provider package's own sentence: it names the provider and
        # carries the server's explanation where there was one.
        error = str(e)
    if parser.cut is not None:
        # Kept through the fence that ended the call, the recorder's own
        # where the model opened another block instead; the usage is the
        # stream's own account where the cut came before the final chunk.
        body = "".join(content)[: parser.cut.at]
        content = [body if parser.cut.fenced else body + "\n" + FENCE]
        if final is None:
            final = stream.stats
    reply = _land_reply(session, content, final, reply_kind, reply_speaker)
    if error is not None:
        yield Failed(error)
        return
    report = ReplyReport(final, max_context) if final is not None else None
    stats = report.text() if session.verbose and report is not None else ""
    yield Done(reply=reply, report=report, stats=stats)


def _pieces(found: list[Prose | ToolCall]) -> Iterator[PlayEvent]:
    """What the parser decided, as events: prose as `Text`, a call's
    piece as the `ToolCall` it is."""
    for piece in found:
        yield Text(piece.text) if isinstance(piece, Prose) else piece


def _ending_switched_on(session: Session) -> frozenset[str]:
    """The names of the tools the user answers that the story has
    switched on — whose call ends the reply."""
    settings = StorySettings(session._settings_db, session._store, session._paths.prompts_file)
    return frozenset(
        setting.tool.name
        for setting in settings
        if isinstance(setting, ToolSetting) and setting.enabled and setting.tool.actor is Actor.USER
    )


def _land_reply(
    session: Session,
    content: list[str],
    final: Stats | None,
    reply_kind: str,
    reply_speaker: Character | None,
) -> Message | None:
    """Record what arrived — the reply row (when any text did), the
    usage — and arm the idle extraction pass. The one landing site for
    clean ends, failures, and mid-stream closes."""
    reply: Message | None = None
    if content:
        # The speaker is the character the prompt asked to answer, when
        # it named one; otherwise extraction attributes the reply later
        # (never for the wire). `kind` still marks an ooc reply.
        session._record_turn(
            Message(
                role="assistant",
                body="".join(content),
                kind=reply_kind,
                speaker=reply_speaker.name if reply_speaker else None,
                speaker_id=reply_speaker.id if reply_speaker else None,
                provider=session.provider,
                model=session.model,
            )
        )
        reply = session._messages[-1]
    if final is not None:
        session._store.usage.record(
            session.provider,
            session.model,
            "chat",
            story_id=session.story_id,
            prompt_tokens=final.prompt_tokens,
            completion_tokens=final.completion_tokens,
            cached_tokens=final.cached_tokens,
            duration_seconds=final.total_seconds,
        )
    if reply is not None and session._config.lore_enabled and session.story_id is not None:
        session._worker.schedule(build_job(session))
    return reply
