"""Playing the story: one submitted line, the reply's event stream, and
the takes over it (regenerate, undo).

The stream is the frontend's to drive: iterate to render, close to
cancel. The event vocabulary lives here and is this module's own, in
the order a turn happens: a row that landed is a `*Turn` — `UserTurn`,
`AssistantTurn`, `ToolTurn` — named by the row's role and carrying the
row as recorded; a piece of the reply as it streams is
`AssistantText`, `AssistantReasoning` or `AssistantToolCall` (a piece
of a call, the tool's name and its inside, never a fence — the deltas
are fed through a `ReplyParser`, so neither frontend parses; the
provider's own chunks never leave this module); and exactly one of
`Declined`, `Failed` and `Done` ends the stream. What is RECORDED is
everything that arrived, fences included; a call of a tool the user
answers or otaku runs ends the reply, and nothing past it is read. Closing
mid-stream keeps and records what arrived — Ctrl+C and Ctrl+R are the
frontend closing the generator; a Ctrl+R then simply calls `regenerate`
for the fresh take. A new reply arms the worker's idle-debounced
extraction pass; every submission (submit, regenerate, undo) defers
pending work first.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace

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
from otaku.backend.tools import TOOLS, Actor, Tool, read_call
from otaku.context import syntax, tool_calls
from otaku.context.assembler import (
    AssembledPrompt,
    ContextOverflowError,
    JoinType,
    context_in_force,
    get_join_type,
)
from otaku.context.tool_calls import FENCE, Prose, ReplyParser, ToolCall, parse_reply
from otaku.formatting import format_context
from otaku.providers import OpenAIClient, ProviderError, Reasoning, Reply, Stats, Text, templates
from otaku.store.schema import Character, CompletionType, Message, StoryMode

# A picture is refused over the text wire though the model sees: the
# request is one text, which has no room for one (`Session.can_attach`).
NO_PICTURE_ON_TEXT = "The request is one text here: no picture can ride it."
# An empty line outside the canvas, where an empty line is nothing.
NOTHING_TO_SEND = "Nothing to send: type a line."


@dataclass(frozen=True)
class UserTurn:
    """The user's row landed (autocorrect settled first) — echo it.
    `note` is the record's own line to show dim beside the echo — the
    dice a /roll rolled; "" for every other row."""

    message: Message
    join_type: JoinType  # how the row joins the text before it (`assembler.get_join_type`)
    note: str = ""


@dataclass(frozen=True)
class AssistantText:
    """A piece of the reply's prose, as it streams. The reply's first
    piece, when it is prose, carries how the reply joins the text before
    it (`assembler.get_join_type`), so a frontend drawing the canvas places it
    without a rule of its own; None on every other piece."""

    text: str
    join_type: JoinType | None = None


@dataclass(frozen=True)
class AssistantReasoning:
    """A piece of the model's thinking, as it streams — shown, never
    recorded."""

    text: str


@dataclass(frozen=True)
class AssistantToolCall:
    """A piece of a tool call's inside, as it streams: the tool's name
    and the text, never a fence; `closed` on the piece the closing
    fence ended."""

    name: str
    text: str
    closed: bool


@dataclass(frozen=True)
class AssistantTurn:
    """The model's row landed, as recorded, once its pieces streamed:
    it ends where the stream ended — at a call of a tool the user
    answers or otaku runs, the closing fence there whether or not the
    model wrote one, or at the reply's own end — and it is told before
    a tool runs, at the end, and when the stream failed alike. `join_type` is
    how it joins the text before it (`assembler.get_join_type`), for the
    canvas."""

    message: Message
    join_type: JoinType


@dataclass(frozen=True)
class ToolTurn:
    """A tool ran and its row landed: the result its body, the tool's
    name its `provider` (`tools.get_tool_label` names it for a reader). The reply
    that continues from it streams next."""

    message: Message
    join_type: JoinType


@dataclass(frozen=True)
class Declined:
    """The stream declines mid-way — it ends here; what already happened
    (a row landed) stands. `reason` is the sentence to show. Named
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
    """The turn's end: what the reply came to (None when the stream
    said nothing of itself) and the verbose stats line — the report's
    `text()`, "" unless /set verbose, so a frontend prints it as it is.
    Every row landed before it, each told by its own event. A reply
    that ended on a question is in the reply's own segments, for a
    frontend to pose: the next line played is the answer, and nothing
    more is built around answering."""

    report: "ReplyReport | None"
    stats: str


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
    max_context: int | None  # the context the prompt was cut to: the window, under the cap

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
        """Percent of the context the prompt ACTUALLY took — the engine's
        own count of it, where `/context` projects the next one; None
        where either figure is unknown."""
        if self.stats.prompt_tokens is None or not self.max_context:
            return None
        return round(100 * self.stats.prompt_tokens / self.max_context)

    def text(self) -> str:
        """The verbose stats line:

            [ total 1.3s, prompt 40 tok, eval 37 tok @ 35.2 tok/s, actual ctx 12% / 32K ]

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
            parts.append(f"actual ctx {self.context_used}% / {format_context(self.max_context)}")
        return f"[ {', '.join(parts)} ]"


@dataclass
class _Attempt:
    """One request's take, as it streams: what arrived, as the reply
    will be recorded (`content`), the usage the final chunk carried, the
    parser that told the tool calls apart, and the sentence a failed
    stream left. Mutable: a close mid-stream lands what is here."""

    parser: ReplyParser
    content: list[str] = field(default_factory=list)
    final: Stats | None = None
    error: str | None = None


PlayEvent = (
    UserTurn
    | AssistantText
    | AssistantReasoning
    | AssistantToolCall
    | AssistantTurn
    | ToolTurn
    | Declined
    | Failed
    | Done
)

# How many times one turn may call a tool otaku runs before the reply
# stands as it is: a model that calls in a loop gets this many results
# and no more.
_MAX_TOOL_ROUNDS = 3


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
    spelling, the turn records, UserTurn is yielded, and the reply
    streams as AssistantReasoning/AssistantText pieces and lands as
    AssistantTurn, Failed on a stream error, Done at the end. With no
    model selected the turn is still recorded — it is story — and
    Declined(NO_MODEL_HINT) ends the stream after UserTurn.
    Closing the generator mid-stream records the partial reply and its
    usage. A new reply arms the worker's idle extraction pass —
    [lore_extraction].enabled gates ONLY this automatic arming; the
    forced /extract path always works. The line is recorded in the
    layout's mode: in story mode the breaks at its edges kept — the
    leading a paragraph it opens, the trailing one it leaves for the
    model's continuation (`assembler.get_join_type`) — and the spaces there
    dropped; in chat mode trimmed, whatever a frontend sent."""
    mode = session.layout.mode
    line = _edge_breaks(line) if mode is StoryMode.STORY else line.strip()
    frame = syntax.read(line)
    error = frame.check()
    if error is not None:
        raise Refused(error)
    # Every picture read here, eagerly like the syntax: a refusal leaves
    # the story and the folder untouched, and nothing is saved before
    # the turn records.
    if files and not session.vision:
        raise Refused(CANNOT_SEE)
    if files and not session.can_attach:
        raise Refused(NO_PICTURE_ON_TEXT)
    if len(files) > MAX_PICTURES:
        raise Refused(TOO_MANY)
    # An empty line is a CONTINUE in the canvas — the story goes on where
    # it stands, no turn recorded (`_submit_events`), over either wire —
    # and nothing at all in the other layouts: refused, never an empty
    # row.
    if not line.strip() and not files and mode is not StoryMode.STORY:
        raise Refused(NOTHING_TO_SEND)
    return _submit_events(session, frame, line, [read_picture(file) for file in files])


def regenerate(session: Session) -> Iterator[PlayEvent]:
    """Re-run the last prompt: the standing reply becomes a sibling and
    the fresh take streams (the reply's pieces and its AssistantTurn,
    Failed/Done — no UserTurn; the prompt is already on screen or in
    `session.messages`). The dropped
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


def join_type(previous: Message | None, next: Message) -> str:
    """How the stored turn `next` joins `previous`, the one before it, for
    a frontend that draws the canvas: "inline" or "paragraph"
    (`context.assembler.get_join_type`, the wire's own rule, read off the
    rows' mode and the breaks at their edges)."""
    return get_join_type(previous, next).value


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
            out.append({"kind": "tool_call", **read_call(part.name, part.text).to_json()})
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
    if not line.strip() and not pictures:
        # A continue: the reply follows the story as it stands — another
        # of the model's rows, joined to the last by the join rule
        # (`assembler.get_join_type`) — and no user row is made, so
        # nothing is echoed and undo takes the continuation alone.
        yield from _reply_events(session, reply_kind=frame.reply_kind, reply_speaker=None)
        return
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
            mode=session.layout.mode,
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
    yield UserTurn(session._messages[-1], _join_of_last(session), note=frame.note)
    reply_speaker = known if frame.speaks == "reply" else None
    yield from _reply_events(session, reply_kind=frame.reply_kind, reply_speaker=reply_speaker)


def _regenerate_events(session: Session) -> Iterator[PlayEvent]:
    session.defer()
    popped = session._regen_pop()
    # The prompt the take re-runs — absent when the story is a promptless
    # reply, or the take was a CONTINUE after the model's own row, which
    # regenerates on its own.
    prompt = session._messages[-1] if session._messages else None
    if prompt is not None and prompt.role != "user":
        prompt = None
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
        assembled = session.assemble(max_context)
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
    # The wire is the session's (`completion_type`, `_request`). A reply
    # is recorded in the layout's mode over either wire, in story mode a
    # break it opens with kept: the paragraph it starts (`get_join_type`).
    resolved = session.text_template if session.completion_type is CompletionType.TEXT else None
    mode = session.layout.mode
    # A text completion that comes back EMPTY, its stream ending clean —
    # an instruct model at the end of a closed paragraph ends its turn at
    # once, every time (Gemma 4, measured) — is asked ONCE more with a
    # paragraph break appended, and the break is recorded as the reply's
    # lead, so the story renders again exactly as it was continued.
    openers = ("", "\n\n") if resolved is not None else ("",)
    # A reply that ends on a call of a tool otaku runs is landed as it
    # stands, the tool's result is recorded as a turn of its own and
    # told, and the model is asked again over the story as it now
    # stands — `_MAX_TOOL_ROUNDS` times at most, the last result
    # recorded but not asked about.
    attempt = _Attempt(ReplyParser(ending=_ending_switched_on(session)))
    for round_ in range(_MAX_TOOL_ROUNDS + 1):
        if round_:
            try:
                assembled = session.assemble(max_context)
            except ContextOverflowError as e:
                yield Declined(str(e))
                return
        # The row the reply goes on from: how it joins is told with its
        # first piece (`AssistantText.join_type`).
        last = session._messages[-1] if session._messages else None
        for opener in openers:
            # The tool calls told apart from the prose as they stream. A
            # call of a tool the user answers or otaku runs ends the reply where
            # its closing fence does: the stream is cut there, and nothing
            # rides the request for it — the rule is otaku's, the same on
            # every engine.
            attempt = _Attempt(ReplyParser(ending=_ending_switched_on(session)))
            stream = _request(session, client, assembled, resolved, opener)
            try:
                yield from _drain(stream, attempt, opener, last=last, mode=mode)
            except (GeneratorExit, KeyboardInterrupt):
                # The frontend closed the stream mid-way, or the terminal's
                # Ctrl+C landed in the wait itself, inside this frame — the
                # same cancel-and-keep. No more yields are possible: record
                # and re-raise. What the stream came to so far is filed too
                # — the prefill was spent and billed, whatever the reader
                # saw.
                _land_reply(session, attempt.content, stream.stats, reply_kind, reply_speaker, mode)
                raise
            except Exception as e:  # the stream failed; what streamed is kept
                # The provider package's own sentence: it names the provider
                # and carries the server's explanation where there was one.
                attempt.error = str(e)
            if attempt.content or attempt.error is not None:
                break
        content, final = attempt.content, attempt.final
        tool_call: Tool | None = None
        if attempt.parser.cut is not None:
            # Kept through the fence that ended the call, the recorder's own
            # where the model opened another block instead; the usage is
            # the stream's own account where the cut came before the final
            # chunk.
            body = "".join(content)[: attempt.parser.cut.at]
            content = [body if attempt.parser.cut.fenced else body + "\n" + FENCE]
            if final is None:
                final = stream.stats
            tool_call = _last_tool_call(content[0])
        reply = _land_reply(session, content, final, reply_kind, reply_speaker, mode)
        if reply is not None:
            yield AssistantTurn(reply, _join_of_last(session))
        if attempt.error is not None:
            yield Failed(attempt.error)
            return
        if tool_call is None or tool_call.actor is not Actor.SYSTEM or reply is None:
            break
        yield ToolTurn(_land_tool_turn(session, tool_call), _join_of_last(session))
    # Measured against what the story was cut to: the model's window under
    # the configured cap — unknown with the window.
    in_force = context_in_force(max_context, session.max_context_setting) if max_context else None
    report = ReplyReport(final, in_force) if final is not None else None
    stats = report.text() if session.verbose and report is not None else ""
    yield Done(report=report, stats=stats)


def _request(
    session: Session,
    client: OpenAIClient,
    assembled: AssembledPrompt,
    resolved: templates.TextTemplate | None,
    opener: str,
) -> Reply:
    """The request the wire sends (`Session.completion_type`), the same stream
    coming back either way. In the text wire (`resolved`, the model's text
    template) it is ONE TEXT the model continues over the engine's text
    wire — the assembly's own rendering (`AssembledPrompt.as_text`, the
    one `/context` previews), `opener` appended (a retry's paragraph
    break) — with the template's turn markers ending it beside the
    reader's own stop strings. Every other story goes as the chat, with
    the messages. The thread this runs on belongs to the frontend between
    tokens, if the frontend said what to do with it (`on_idle`)."""
    params = dict(session.params)
    if resolved is None:
        return client.completion.chat(
            session.model,
            assembled.messages,
            params,
            level=session.think,
            purpose="chat",
            on_idle=session._on_idle,
        )
    stops = params.get("stop")
    params["stop"] = [*(stops if isinstance(stops, list) else []), *resolved.stops]
    return client.completion.text(
        session.model,
        assembled.as_text(resolved, opener),
        params,
        # No thinking knob rides the text wire: the rendered turn has no
        # thinking channel to budget, and omlx's engine handed a budget of
        # 0 on a raw prompt returns nothing more often than not (Gemma 4,
        # measured).
        level=None,
        purpose="chat",
        on_idle=session._on_idle,
    )


def _drain(
    stream: Reply, attempt: _Attempt, opener: str, *, last: Message | None, mode: StoryMode
) -> Iterator[PlayEvent]:
    """One attempt's chunks as events — reasoning as it comes, prose and
    a call's pieces as the parser tells them apart — filed in `attempt`
    as they go, so what is recorded is exactly what was yielded. The
    reply's first piece, when prose, says how it joins `last`. The
    stream is closed when the attempt ends, however it ends."""
    held = ""  # a whitespace run the stream has not yet earned sending
    join: JoinType | None = None  # told with the first piece, then spent
    try:
        for chunk in stream:
            if isinstance(chunk, Reasoning):
                yield AssistantReasoning(chunk.text)
            elif isinstance(chunk, Text):
                # Some models pad the reply with blank lines. The text
                # starts at its first real character — a story-mode reply
                # keeps the breaks before it, the paragraph it opens — and
                # a trailing whitespace run is held back, sent only when
                # more text follows it, dropped at the stream's end.
                text = held + chunk.text
                held = ""
                if not attempt.content:
                    text = _lead_breaks(text) if mode is StoryMode.STORY else text.lstrip()
                stripped = text.rstrip()
                held, text = text[len(stripped) :], stripped
                if not text:
                    continue
                if not attempt.content:
                    # the break a retry opened, the paragraph it writes in
                    text = opener + text.lstrip("\n") if opener else text
                    probe = Message(role="assistant", body=text, mode=mode)
                    join = get_join_type(last, probe)
                attempt.content.append(text)
                for piece in _pieces(attempt.parser.feed(text)):
                    if join is not None and isinstance(piece, AssistantText):
                        piece = replace(piece, join_type=join)
                    join = None
                    yield piece
                if attempt.parser.cut is not None:
                    break
            elif isinstance(chunk, Stats):
                attempt.final = chunk
        if attempt.parser.cut is None:
            yield from _pieces(attempt.parser.flush())
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()


def _pieces(found: list[Prose | ToolCall]) -> Iterator[PlayEvent]:
    """What the parser decided, as events: prose as `AssistantText`, a
    call's piece as `AssistantToolCall`."""
    for piece in found:
        if isinstance(piece, Prose):
            yield AssistantText(piece.text)
        else:
            yield AssistantToolCall(piece.name, piece.text, piece.closed)


def _ending_switched_on(session: Session) -> frozenset[str]:
    """The names of the tools the user answers or otaku runs that the
    story has switched on — whose call ends the reply."""
    settings = StorySettings(
        session._settings_db,
        session._store,
        session._paths.prompts_file,
        can_complete_text=session.can_complete_text,
    )
    return frozenset(
        setting.tool.name
        for setting in settings
        if isinstance(setting, ToolSetting)
        and setting.enabled
        and setting.tool.actor in (Actor.USER, Actor.SYSTEM)
    )


def _last_tool_call(body: str) -> Tool | None:
    """The reply's last tool call, read as its tool — the one that ended
    it; None where the body ends on none or on a tool this build does
    not have."""
    calls = [piece for piece in parse_reply(body) if isinstance(piece, ToolCall)]
    if not calls:
        return None
    try:
        return read_call(calls[-1].name, calls[-1].text)
    except KeyError:
        return None


def _edge_breaks(text: str) -> str:
    """`text` with only the line breaks of the whitespace at its edges
    left: a break there is a paragraph the row opens or leaves open, and
    a space decides nothing (`assembler.get_join_type`)."""
    core = text.strip()
    if not core:
        return "\n" * text.count("\n")
    lead = text[: text.index(core)]
    tail = text[len(lead) + len(core) :]
    return "\n" * lead.count("\n") + core + "\n" * tail.count("\n")


def _lead_breaks(text: str) -> str:
    """`text` with only the line breaks of its leading whitespace left:
    a reply's start in story mode, a break there the paragraph it opens.
    Its end is the stream's to settle, a piece's trailing whitespace held
    until more text follows it (`_drain`)."""
    core = text.lstrip()
    return "\n" * text[: len(text) - len(core)].count("\n") + core


def _join_of_last(session: Session) -> JoinType:
    """How the row just recorded joins the one before it — told with the
    row, so a frontend drawing the canvas places it without a second
    read (`assembler.get_join_type`)."""
    rows = session._messages
    return get_join_type(rows[-2] if len(rows) > 1 else None, rows[-1])


def _land_tool_turn(session: Session, tool_call: Tool) -> Message:
    """Run the tool and record its result as a TOOL turn — nobody's
    line, the tool's name its `provider` — and hand the row back for
    the frontends to draw."""
    # the tool reads what it needs off the session, and writes nothing
    result = tool_call.run(session)
    session._record_turn(
        Message(
            role="tool",
            kind="tool",
            body=result,
            provider=tool_call.name,
            mode=session.layout.mode,
        )
    )
    return session._messages[-1]


def _land_reply(
    session: Session,
    content: list[str],
    final: Stats | None,
    reply_kind: str,
    reply_speaker: Character | None,
    mode: StoryMode,
) -> Message | None:
    """Record what arrived — the reply row (when any text did), the
    usage — and arm the idle extraction pass. The one landing site for
    clean ends, failures, and mid-stream closes. `mode` is the one the
    reply was written in."""
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
                mode=mode,
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
