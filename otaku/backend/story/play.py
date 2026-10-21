"""Playing a story: one submitted line, the reply's stream, the takes
over it (regenerate, undo).

The stream is the engine's own events, in the order a turn happens: a
row that landed — the reader's line, the model's reply, a tool's result
— is `Landed`, the row as recorded and the one before it; a piece of the
reply as it streams is `ProsePiece`, `ReasoningPiece` or `CallPiece` (a
piece of a call, the tool's name and its inside, never a fence — the
deltas are fed through a `ReplyParser`, so nothing above parses; the
provider's own chunks never leave this module); and exactly one of
`Declined`, `Failed` and `Finished` ends it. What is RECORDED is
everything that arrived, fences included; a call of a tool the user
answers or otaku runs ends the reply, and nothing past it is read.
Closing mid-stream keeps and records what arrived. A new reply arms the
worker's idle-debounced extraction pass; every submission (submit,
regenerate, undo) defers pending work first.
"""

from collections.abc import Generator, Iterator, Sequence
from dataclasses import dataclass, field, replace

from otaku.backend.app.core.services import App
from otaku.backend.app.pictures.services import Picture, RawFile, read_picture, save
from otaku.backend.errors import NO_MODEL_HINT, NO_SUCH_STORY, Conflict, Invalid, NotFound
from otaku.backend.story.chain import append_message, check_head, pop_reply, unit_start
from otaku.backend.story.request import assemble, build_job
from otaku.backend.story.settings.services import StorySettings, ToolSetting, settings_of
from otaku.backend.story.tools import TOOLS, Actor, Tool, read_call
from otaku.context import syntax
from otaku.context.assembler import (
    AssembledPrompt,
    ContextOverflowError,
    JoinType,
    context_in_force,
    get_join_type,
)
from otaku.context.tool_calls import FENCE, Prose, ReplyParser, ToolCall, parse_reply
from otaku.providers import OpenAIClient, ProviderError, Reasoning, Reply, Stats, Text, templates
from otaku.settings.state import ModelRef
from otaku.store.files import Owner
from otaku.store.schema import Character, CompletionType, Layout, Message, StoryMode

# How many pictures one turn may carry, and the refusals that are not
# about one file: a model that cannot see, too many on one turn.
_MAX_PICTURES = 8
_CANNOT_SEE = "This model cannot see pictures."
_TOO_MANY = f"At most {_MAX_PICTURES} pictures on one turn."
# A picture is refused over the text wire though the model sees: the
# request is one text, which has no room for one (`ModelFacts.can_attach`).
NO_PICTURE_ON_TEXT = "The request is one text here: no picture can ride it."
# An empty line outside the canvas, where an empty line is nothing.
NOTHING_TO_SEND = "Nothing to send: type a line."
# A regenerate with no reply to take again — no story at all included.
NOTHING_TO_REGENERATE = "Nothing to regenerate."


@dataclass(frozen=True)
class Landed:
    """A row the stream recorded — the reader's line (autocorrect
    settled first), the model's reply, a tool's result — the row before
    it on the chain, which it joins (None for the first), and the first
    row of the reply unit it stands in (`chain.unit_start`). `note` is
    the record's own line to show beside it: the dice a /roll rolled; ""
    for every other row."""

    row: Message
    previous: Message | None
    unit: int
    note: str = ""


@dataclass(frozen=True)
class ProsePiece:
    """A piece of the reply's prose, as it streams. The reply's first
    piece, when it is prose, carries how the reply joins the text before
    it (`assembler.get_join_type`); None on every other piece."""

    text: str
    join_type: JoinType | None = None


@dataclass(frozen=True)
class ReasoningPiece:
    """A piece of the model's thinking, as it streams — shown, never
    recorded."""

    text: str


@dataclass(frozen=True)
class CallPiece:
    """A piece of a tool call's inside, as it streams: the tool's name
    and the text, never a fence; `closed` on the piece the closing
    fence ended; the tool's `label` and who acts on its call — "" and
    None for a tool this build does not have."""

    name: str
    text: str
    closed: bool
    label: str = ""
    actor: Actor | None = None


@dataclass(frozen=True)
class Declined:
    """The stream declines mid-way — it ends here; what already happened
    (a row landed) stands. `reason` is the sentence to show."""

    reason: str


@dataclass(frozen=True)
class Failed:
    """The stream failed; what already streamed is kept and recorded.
    `reason` is the sentence to show."""

    reason: str


@dataclass(frozen=True)
class Finished:
    """The turn's end: the stream's own account of the reply (None when
    it said nothing of itself), the context the prompt was cut to — the
    window under the cap, None while unknown — and whether the stats line
    is shown (`/set verbose`)."""

    stats: Stats | None
    context_in_force: int | None
    verbose: bool


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


Event = Landed | ProsePiece | ReasoningPiece | CallPiece | Declined | Failed | Finished

# How many times one turn may call a tool otaku runs before the reply
# stands as it is: a model that calls in a loop gets this many results
# and no more.
_MAX_TOOL_ROUNDS = 3


def submit_line(
    app: App,
    story_id: int,
    line: str,
    files: Sequence[RawFile] = (),
    *,
    expected_head: int | None,
) -> Generator[Event, None, None]:
    """One submitted line of a story (prose or a direction — never a
    command; the frontend routed those already), with `files` the
    pictures the reader attached to it — a line may be empty when it
    carries one — played on from `expected_head`, the head the caller
    last saw. Validation is EAGER: invalid syntax
    raises Refused (the usage line) before the iterator is returned and
    before anything is recorded —
    the body is a plain function that validates and returns the inner
    generator, never a generator itself — and so does every picture:
    made ready here, refused here (the model cannot see, too many, a
    file that is no picture), stored only when the turn records, so a
    refusal leaves the story and the folder untouched. Then: the typed
    name settles to the cast's
    spelling, the turn records and is told (`Landed`), and the reply
    streams as its pieces and lands, `Failed` on a stream error,
    `Finished` at the end. With no model selected the turn is still
    recorded — it is story — and Declined(NO_MODEL_HINT) ends the stream
    after it.
    Closing the generator mid-stream records the partial reply and its
    usage. A new reply arms the worker's idle extraction pass —
    [lore_extraction].enabled gates ONLY this automatic arming; the
    forced /extract path always works. The line is recorded in the
    layout's mode: in story mode the breaks at its edges kept — the
    leading a paragraph it opens, the trailing one it leaves for the
    model's continuation (`assembler.get_join_type`) — and the spaces there
    dropped; in chat mode trimmed, whatever a frontend sent. Raises
    NotFound for a story that is not there, HeadMoved where its head
    stands elsewhere than expected — eagerly too."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    line, frame, pictures = _checked(app, line, files, settings_of(app, story_id).layout)
    chain = app._store.stories.get_messages(story_id)
    check_head(chain, expected_head)
    return _submit_events(app, story_id, chain, frame, line, pictures)


def _checked(
    app: App, line: str, files: Sequence[RawFile], layout: Layout
) -> tuple[str, syntax.Line, list[Picture]]:
    """A line made ready to play in `layout`: as it is recorded, read in
    the story's language, and its pictures read — or Refused."""
    facts = app._model_facts()
    mode = layout.mode
    line = _edge_breaks(line) if mode is StoryMode.STORY else line.strip()
    frame = syntax.read(line)
    error = frame.check()
    if error is not None:
        raise Invalid(error)
    # Every picture read here, eagerly like the syntax: a refusal leaves
    # the story and the folder untouched, and nothing is saved before
    # the turn records.
    if files and not facts.vision:
        raise Conflict(_CANNOT_SEE)
    if files and not facts.can_attach(layout):
        raise Conflict(NO_PICTURE_ON_TEXT)
    if len(files) > _MAX_PICTURES:
        raise Invalid(_TOO_MANY)
    # An empty line is a CONTINUE in the canvas — the story goes on where
    # it stands, no turn recorded (`_submit_events`), over either wire —
    # and nothing at all in the other layouts: refused, never an empty
    # row.
    if not line.strip() and not files and mode is not StoryMode.STORY:
        raise Invalid(NOTHING_TO_SEND)
    return line, frame, [read_picture(file) for file in files]


def regenerate_reply(
    app: App, story_id: int, *, expected_head: int | None
) -> Generator[Event, None, None]:
    """Re-run a story's last prompt: the standing reply becomes a sibling
    and the fresh take streams (the reply's pieces and its row, Failed or
    Finished — no line of the reader's; the prompt is already on
    screen). The
    dropped reply's kind and speaker are re-derived from the prompt,
    exactly as when it first played. Raises Refused when there is no
    model, no story, nothing to regenerate, or a head standing elsewhere
    than `expected_head` — EAGERLY, before the iterator is returned and
    before anything is dropped (the same validate-then-return-generator
    shape as `submit_line`)."""
    if app._client(app._model()) is None:
        # Before anything is dropped: without a model there is no fresh
        # take, and the standing reply must survive untouched.
        raise Conflict(NO_MODEL_HINT)
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    chain = app._store.stories.get_messages(story_id)
    if not chain:
        raise Conflict(NOTHING_TO_REGENERATE)
    check_head(chain, expected_head)
    return _regenerate_events(app, story_id, expected_head=chain[-1].id)


# ---------- the stream internals ----------


def _submit_events(
    app: App,
    story_id: int,
    chain: list[Message],
    frame: syntax.Line,
    line: str,
    pictures: Sequence[Picture],
) -> Generator[Event, None, None]:
    """`submit`'s stream over the story's chain as it was read, which it
    keeps current as its rows land."""
    app.note_submission()
    if not line.strip() and not pictures:
        # A continue: the reply follows the story as it stands — another
        # of the model's rows, joined to the last by the join rule
        # (`assembler.get_join_type`) — and no user row is made, so
        # nothing is echoed and undo takes the continuation alone.
        yield from _reply_events(
            app, story_id, chain, reply_kind=frame.reply_kind, reply_speaker=None
        )
        return
    # The named character, resolved once and used three ways: the
    # autocorrect rewrite, the request's speaker (/me — the line IS their
    # words), and the reply's (/you — it asks them to answer). Safe to
    # act on because `characters.find` matches an exact name or alias
    # only: it can settle a spelling or attribute a line, never pick
    # someone else. The name guard is the hot path's: a prose line names
    # nobody and must not query the cast for it.
    known = app._store.characters.find(story_id, frame.name) if frame.name else None
    if known is not None and app._state_config().autocorrect:
        # Settled BEFORE anything sees it, so the echo, the store, and
        # the wire all read one text — a played line never moves after.
        line = frame.update_name(known.name)
    request_speaker = known if frame.speaks == "request" else None
    # The pictures go into the folder before the turn records, under the
    # story's number. Nothing was written before this point.
    attachments = tuple(
        save(app._store.files, picture, Owner.STORY, story_id) for picture in pictures
    )
    row = append_message(
        app,
        story_id,
        Message(
            role="user",
            body=line,
            mode=settings_of(app, story_id).layout.mode,
            kind=frame.request_kind,
            # Resolved at RECORD time — the template is data on the turn,
            # filled with the line's own name and text only at wire time.
            # `freeze_template` bakes in what must never re-compose: a
            # /roll's dice are decided here, once, and never again.
            template=frame.freeze_template(
                getattr(app._prompts(), frame.template_field) if frame.template_field else None
            ),
            speaker=request_speaker.name if request_speaker else None,
            speaker_id=request_speaker.id if request_speaker else None,
            attachments=attachments,
        ),
        # From the head the chain was read at: the line goes after the
        # turns the caller saw, or nowhere.
        after=chain[-1].id if chain else None,
    )
    chain.append(row)
    yield _landed(chain, note=frame.note)
    reply_speaker = known if frame.speaks == "reply" else None
    yield from _reply_events(
        app, story_id, chain, reply_kind=frame.reply_kind, reply_speaker=reply_speaker
    )


def _regenerate_events(
    app: App, story_id: int, *, expected_head: int | None
) -> Generator[Event, None, None]:
    """`regenerate`'s stream: the standing reply set aside, from the head
    `regenerate` read, and the fresh take over what stands before it."""
    app.note_submission()
    popped, chain = pop_reply(app, story_id, expected_head=expected_head)
    # The prompt the take re-runs — absent when the story is a promptless
    # reply, or the take was a CONTINUE after the model's own row, which
    # regenerates on its own.
    prompt = chain[-1] if chain else None
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
        if frame.speaks == "reply":
            speaker = app._store.characters.find(story_id, frame.name)
    else:
        kind = popped.kind if popped is not None else "dialogue"
    yield from _reply_events(app, story_id, chain, reply_kind=kind, reply_speaker=speaker)


def _reply_events(
    app: App,
    story_id: int,
    chain: list[Message],
    *,
    reply_kind: str,
    reply_speaker: Character | None,
) -> Iterator[Event]:
    """Stream one completion for a story as `chain` holds it, yielding
    deltas, and record whatever arrived — on a clean end, a failure, and
    a mid-stream close alike; `chain` follows every row that lands.
    `reply_kind` is the kind the REPLY is stored under — never inferred
    from the prompt's own kind, because a /you
    switch also ends on an ((OOC:)) turn yet wants an in-character
    answer. `reply_speaker` is the cast member the reply is spoken by,
    when the prompt named one (/you): stored on the reply row, where
    extraction's own labeling is fill-only, so it stands."""
    model = app._model()
    client = app._client(model)
    if model is None or client is None:
        # The turn is recorded — it is story — and plays on a regenerate
        # once a model exists.
        yield Declined(NO_MODEL_HINT)
        return
    try:
        # A cold model is loaded first, so the window the prompt is cut
        # to is the runner's and not a substitute (`OpenAIModels.ready`).
        found = client.models.ready(model.name, on_idle=app._on_idle)
        context_window = found.context_window if found else None
        assembled = assemble(app, story_id, context_window, chain)
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
    # The wire is the layout's on the model in use (`ModelFacts.
    # completion_type`, `_request`). A reply is recorded in the layout's
    # mode over either wire, in story mode a break it opens with kept:
    # the paragraph it starts (`get_join_type`).
    settings = settings_of(app, story_id)
    layout = settings.layout
    facts = app._model_facts()
    text_wire = facts.completion_type(layout) is CompletionType.TEXT
    resolved = facts.text_template if text_wire else None
    mode = layout.mode
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
    ending = _ending_switched_on(settings)
    attempt = _Attempt(ReplyParser(ending=ending))
    for round_ in range(_MAX_TOOL_ROUNDS + 1):
        if round_:
            try:
                assembled = assemble(app, story_id, context_window, chain)
            except ContextOverflowError as e:
                yield Declined(str(e))
                return
        # The row the reply goes on from: how it joins is told with its
        # first piece (`ProsePiece.join_type`).
        last = chain[-1] if chain else None
        for opener in openers:
            # The tool calls told apart from the prose as they stream. A
            # call of a tool the user answers or otaku runs ends the reply where
            # its closing fence does: the stream is cut there, and nothing
            # rides the request for it — the rule is otaku's, the same on
            # every engine.
            attempt = _Attempt(ReplyParser(ending=ending))
            stream = _request(app, client, model, assembled, resolved, opener)
            try:
                yield from _drain(stream, attempt, opener, last=last, mode=mode)
            except (GeneratorExit, KeyboardInterrupt):
                # The frontend closed the stream mid-way, or the terminal's
                # Ctrl+C landed in the wait itself, inside this frame — the
                # same cancel-and-keep. No more yields are possible: record
                # and re-raise. What the stream came to so far is filed too
                # — the prefill was spent and billed, whatever the reader
                # saw.
                _land_reply(
                    app,
                    story_id,
                    model,
                    chain,
                    attempt.content,
                    stream.stats,
                    reply_kind,
                    reply_speaker,
                    mode,
                )
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
        reply = _land_reply(
            app, story_id, model, chain, content, final, reply_kind, reply_speaker, mode
        )
        if reply is not None:
            yield _landed(chain)
        if attempt.error is not None:
            yield Failed(attempt.error)
            return
        if tool_call is None or tool_call.actor is not Actor.SYSTEM or reply is None:
            break
        _land_tool_message(app, story_id, chain, tool_call, mode)
        yield _landed(chain)
    # Measured against what the story was cut to: the model's window under
    # the configured cap — unknown with the window.
    limit = app._config().context_limit
    in_force = context_in_force(context_window, limit) if context_window else None
    yield Finished(final, in_force, verbose=app._state_config().verbose)


def _request(
    app: App,
    client: OpenAIClient,
    model: ModelRef,
    assembled: AssembledPrompt,
    resolved: templates.TextTemplate | None,
    opener: str,
) -> Reply:
    """The request the wire sends (`ModelFacts.completion_type`), the same stream
    coming back either way. In the text wire (`resolved`, the model's text
    template) it is ONE TEXT the model continues over the engine's text
    wire — the assembly's own rendering (`AssembledPrompt.as_text`, the
    one `/context` previews), `opener` appended (a retry's paragraph
    break) — with the template's turn markers ending it beside the
    reader's own stop strings. Every other story goes as the chat, with
    the messages. The thread this runs on belongs to the frontend between
    tokens, if the frontend said what to do with it (`on_idle`)."""
    saved = app._model_settings(model.name)
    params = dict(saved.params)
    if resolved is None:
        return client.completion.chat(
            model.name,
            assembled.messages,
            params,
            level=saved.level,
            purpose="chat",
            on_idle=app._on_idle,
        )
    stops = params.get("stop")
    params["stop"] = [*(stops if isinstance(stops, list) else []), *resolved.stops]
    return client.completion.text(
        model.name,
        assembled.as_text(resolved, opener),
        params,
        # No thinking knob rides the text wire: the rendered turn has no
        # thinking channel to budget, and omlx's engine handed a budget of
        # 0 on a raw prompt returns nothing more often than not (Gemma 4,
        # measured).
        level=None,
        purpose="chat",
        on_idle=app._on_idle,
    )


def _drain(
    stream: Reply, attempt: _Attempt, opener: str, *, last: Message | None, mode: StoryMode
) -> Iterator[Event]:
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
                yield ReasoningPiece(chunk.text)
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
                    if join is not None and isinstance(piece, ProsePiece):
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


def _pieces(found: list[Prose | ToolCall]) -> Iterator[Event]:
    """What the parser decided, as events: prose as `ProsePiece`, a
    call's piece as `CallPiece`."""
    for piece in found:
        if isinstance(piece, Prose):
            yield ProsePiece(piece.text)
        else:
            known = TOOLS.get(piece.name)
            yield CallPiece(
                piece.name,
                piece.text,
                piece.closed,
                known.label if known is not None else "",
                known.actor if known is not None else None,
            )


def _ending_switched_on(settings: StorySettings) -> frozenset[str]:
    """The names of the tools the user answers or otaku runs that a
    story's settings switch on — whose call ends the reply."""
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


def _landed(chain: Sequence[Message], *, note: str = "") -> Landed:
    """The row just recorded — the chain's last — as it is told."""
    first = chain[unit_start(chain, len(chain))]
    assert first.id is not None  # a recorded row has its id
    return Landed(chain[-1], chain[-2] if len(chain) > 1 else None, first.id, note)


def _land_tool_message(
    app: App, story_id: int, chain: list[Message], tool_call: Tool, mode: StoryMode
) -> Message:
    """Run the tool and record its result as a TOOL turn of the story —
    nobody's line, the tool's name its `provider` — and hand the row
    back for the frontends to draw."""
    # the tool reads what it needs off the app, and writes nothing
    result = tool_call.run(app)
    row = append_message(
        app,
        story_id,
        Message(role="tool", kind="tool", body=result, provider=tool_call.name, mode=mode),
        after=chain[-1].id if chain else None,
    )
    chain.append(row)
    return row


def _land_reply(
    app: App,
    story_id: int,
    model: ModelRef,
    chain: list[Message],
    content: list[str],
    final: Stats | None,
    reply_kind: str,
    reply_speaker: Character | None,
    mode: StoryMode,
) -> Message | None:
    """Record what arrived on the story — the reply row (when any text
    did), the usage — and arm the idle extraction pass. The one landing
    site for clean ends, failures, and mid-stream closes. `model` is the
    one that wrote it, `mode` the one it was written in."""
    reply: Message | None = None
    if content:
        # The speaker is the character the prompt asked to answer, when
        # it named one; otherwise extraction attributes the reply later
        # (never for the wire). `kind` still marks an ooc reply.
        reply = append_message(
            app,
            story_id,
            Message(
                role="assistant",
                body="".join(content),
                kind=reply_kind,
                speaker=reply_speaker.name if reply_speaker else None,
                speaker_id=reply_speaker.id if reply_speaker else None,
                provider=model.provider,
                model=model.name,
                mode=mode,
            ),
            after=chain[-1].id if chain else None,
        )
        chain.append(reply)
    if final is not None:
        app._store.usage.record(
            model.provider,
            model.name,
            "chat",
            story_id=story_id,
            prompt_tokens=final.prompt_tokens,
            completion_tokens=final.completion_tokens,
            cached_tokens=final.cached_tokens,
            duration_seconds=final.total_seconds,
        )
    if reply is not None and app._config().lore_enabled:
        app._worker.schedule(build_job(app, story_id))
    return reply
