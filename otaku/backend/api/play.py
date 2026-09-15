"""Playing the story: one submitted line, the reply's event stream, and
the takes over it (regenerate, undo).

The stream is the frontend's to drive: iterate to render, close to
cancel. The event vocabulary lives here, `Text` and `Reasoning` included
(re-exported from providers — members of this module's union). Closing
mid-stream keeps and records what arrived — Ctrl+C and Ctrl+R are the
frontend closing the generator; a Ctrl+R then simply calls `regenerate`
for the fresh take. Exactly one of Declined, Failed, or Done ends a
stream. A new reply arms the worker's idle-debounced extraction pass;
every submission (submit, regenerate, undo) defers pending work first.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from otaku.backend.api.cards import drop_unplayed_card
from otaku.backend.api.lore import build_job
from otaku.backend.session import NO_MODEL_HINT, Refused, Session
from otaku.context import syntax
from otaku.context.assembler import ContextOverflowError
from otaku.formatting import format_context
from otaku.providers import Reasoning as Reasoning
from otaku.providers import Stats
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
class Done:
    """The turn's end: the recorded reply (None when nothing arrived) and
    the verbose stats line ("" when off or unknowable)."""

    reply: Message | None
    stats: str


PlayEvent = Recorded | Text | Reasoning | Declined | Failed | Done


def submit(session: Session, line: str) -> Iterator[PlayEvent]:
    """One submitted story line (prose or a direction — never a command;
    the frontend routed those already). Validation is EAGER: invalid
    syntax raises Refused (the usage line) before the iterator is
    returned and before anything is recorded — the body is a plain
    function that validates and returns the inner generator, never a
    generator itself. Then: the typed name settles to the cast's
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
    return _submit_events(session, frame, line)


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


# ---------- the stream internals ----------


def _submit_events(session: Session, frame: syntax.Line, line: str) -> Iterator[PlayEvent]:
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
        found = client.models.get(session.model)
        max_context = found.max_context if found else None
        wire = session.assemble(max_context).messages
    except ContextOverflowError as e:
        # The turn is recorded — it is story — and plays once the limit
        # is raised or more scenes close. The sentence is the assembler's.
        yield Declined(str(e))
        return
    content: list[str] = []
    held = ""  # a whitespace run the stream has not yet earned sending
    final: Stats | None = None
    error: str | None = None
    stream = client.completion.chat(
        session.model,
        wire,
        dict(session.params),
        effort=session.think,
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
                    yield Text(text)
                elif isinstance(chunk, Stats):
                    final = chunk
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
    except (GeneratorExit, KeyboardInterrupt):
        # The frontend closed the stream mid-way, or the terminal's
        # Ctrl+C landed in the wait itself, inside this frame — the same
        # cancel-and-keep. No more yields are possible: record and
        # re-raise.
        _land_reply(session, content, final, reply_kind, reply_speaker)
        raise
    except Exception as e:  # the stream failed; what streamed is kept
        # The provider package's own sentence: it names the provider and
        # carries the server's explanation where there was one.
        error = str(e)
    reply = _land_reply(session, content, final, reply_kind, reply_speaker)
    if error is not None:
        yield Failed(error)
        return
    stats = _format_stats(final, max_context) if session.verbose and final is not None else ""
    yield Done(reply=reply, stats=stats)


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


def _format_stats(stats: Stats, max_context: int | None) -> str:
    """The verbose stats line:

        [ total 1.3s, prompt 40 tok, eval 37 tok @ 35.2 tok/s, ctx 12% / 32K ]

    `total` is wall-clock for the whole request; the rate is computed over
    the decode-only span (excluding prefill and time-to-first-token) so it
    reflects generation speed; `max_context` is the context the prompt
    is measured against. Fields with no underlying value are skipped."""
    parts: list[str] = [f"total {stats.total_seconds:.1f}s"]
    if stats.prompt_tokens is not None:
        parts.append(f"prompt {stats.prompt_tokens} tok")
    if stats.cached_tokens is not None:
        # Zero included: "cached 0 tok" is how a reader discovers their
        # pacing outlives the cache TTL (see providers.toml prompt_cache).
        parts.append(f"cached {stats.cached_tokens} tok")
    if stats.completion_tokens is not None:
        generation = stats.total_seconds - (stats.first_token_seconds or 0.0)
        if generation > 0:
            rate = stats.completion_tokens / generation
            parts.append(f"eval {stats.completion_tokens} tok @ {rate:.1f} tok/s")
        else:
            parts.append(f"eval {stats.completion_tokens} tok")
    if max_context:
        cap = format_context(max_context)
        if stats.prompt_tokens is not None:
            pct = stats.prompt_tokens / max_context * 100
            parts.append(f"ctx {pct:.0f}% / {cap}")
        else:
            parts.append(f"ctx {cap}")
    return "[ " + ", ".join(parts) + " ]"
