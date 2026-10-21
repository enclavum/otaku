"""What `story.messages.resources` answers: its schemas."""

from __future__ import annotations

from collections.abc import Generator, Iterator

# Re-exported: what a picture is handed in as.
from otaku.backend.app.pictures.services import RawFile as RawFile
from otaku.backend.base import Report, Schema
from otaku.backend.story.messages import services as domain_messages
from otaku.backend.story.messages.services import Actor
from otaku.context.assembler import JoinType
from otaku.formatting import format_context
from otaku.providers import Stats
from otaku.store import schema
from otaku.store.schema import Attachment, StoryMode


def as_events(found: Generator[domain_messages.Event, None, None]) -> Iterator[PlayEvent]:
    """The engine's events as a frontend is told them: a landed row as
    the message a client draws, a call's piece with its tool's label and
    who acts on it, the end with the reply's report. Closing this closes
    the engine — what arrived is recorded there."""
    try:
        for happened in found:
            match happened:
                case domain_messages.Landed(row=row, previous=previous, unit=unit, note=note):
                    message = _as_message(row, previous, unit)
                    if row.role == "user":
                        yield UserMessage(message, note=note)
                    elif row.role == "tool":
                        yield ToolMessage(message)
                    else:
                        yield AssistantMessage(message)
                case domain_messages.ProsePiece(text=text, join_type=join_type):
                    yield AssistantText(text, join_type)
                case domain_messages.ReasoningPiece(text=text):
                    yield AssistantReasoning(text)
                case domain_messages.CallPiece(
                    name=name, text=text, closed=closed, label=label, actor=actor
                ):
                    yield AssistantToolCall(
                        name,
                        text,
                        closed,
                        label,
                        actor,
                        f"calling the {(label or name).lower()} tool",
                    )
                case domain_messages.Declined(reason=reason):
                    yield Declined(reason)
                case domain_messages.Failed(reason=reason):
                    yield Failed(reason)
                case domain_messages.Finished(
                    stats=stats, context_in_force=in_force, verbose=verbose
                ):
                    report = ReplyReport(stats, in_force) if stats is not None else None
                    shown = report.text() if verbose and report is not None else ""
                    yield Done(report=report, stats=shown)
    finally:
        found.close()


def _as_message(row: schema.Message, previous: schema.Message | None, unit: int) -> Message:
    """A stored row as the message a client draws: its body split, how
    it joins `previous` (the row before it), a tool message's label, and the
    first row of the reply unit it stands in."""
    return Message(
        id=row.id,
        role=row.role,
        body=row.body,
        kind=row.kind,
        template=row.template,
        speaker=row.speaker,
        provider=row.provider,
        model=row.model,
        attachments=row.attachments,
        mode=row.mode,
        segments=tuple(domain_messages.segments(row.body, row.role)),
        join_type=domain_messages.join_type(previous, row),
        tool_label=domain_messages.tool_label(row),
        unit=unit,
    )


class Message(Schema):
    """A stored message as a client draws it: its id and role, its body as
    stored (fences and all), the kind the language stored it as, the
    speaker (None until extraction names one), who made it (`provider`
    and `model` — a card's file, a tool's name), the template it was
    played with, its pictures, the mode it was written in — and what a
    client would otherwise parse or decide: the body split (`segments`:
    prose, and each tool call as its tool's facts; a user's line its
    story and its words with the model), how it joins the message before
    it (`join_type`), a tool message's label (None on every other), and
    the first message of the reply unit it stands in (`unit`: what an
    undo or a regenerate takes with it)."""

    id: int
    role: str
    body: str
    kind: str
    template: str | None
    speaker: str | None
    provider: str | None
    model: str | None
    attachments: tuple[Attachment, ...]
    mode: StoryMode
    segments: tuple[dict[str, object], ...]
    join_type: JoinType
    tool_label: str | None
    unit: int


class UserMessage(Schema):
    """The user's row landed (autocorrect settled first) — echo it.
    `note` is the record's own line to show dim beside the echo — the
    dice a /roll rolled; "" for every other row."""

    message: Message
    note: str = ""


class AssistantText(Schema):
    """A piece of the reply's prose, as it streams. The reply's first
    piece, when it is prose, carries how the reply joins the text before
    it (`assembler.get_join_type`), so a frontend drawing the canvas places it
    without a rule of its own; None on every other piece."""

    text: str
    join_type: JoinType | None = None


class AssistantReasoning(Schema):
    """A piece of the model's thinking, as it streams — shown, never
    recorded."""

    text: str


class AssistantToolCall(Schema):
    """A piece of a tool call's inside, as it streams: the tool's name
    and the text, never a fence; `closed` on the piece the closing
    fence ended; the tool's `label` and who acts on its call — "" and
    None for a tool this build does not have — and what a status row
    says while the call is made (`status`)."""

    name: str
    text: str
    closed: bool
    label: str
    actor: Actor | None
    status: str


class AssistantMessage(Schema):
    """The model's row landed, as recorded, once its pieces streamed:
    it ends where the stream ended — at a call of a tool the user
    answers or otaku runs, the closing fence there whether or not the
    model wrote one, or at the reply's own end — and it is told before
    a tool runs, at the end, and when the stream failed alike — the
    message carrying how it joins the text before it, for the canvas."""

    message: Message


class ToolMessage(Schema):
    """A tool ran and its row landed: the result its body, the tool's
    name its `provider`, its `tool_label` what a reader calls it. The
    reply that continues from it streams next."""

    message: Message


class Declined(Schema):
    """The stream declines mid-way — it ends here; what already happened
    (a row landed) stands. `reason` is the sentence to show. Named
    apart from the `Refused` exception on purpose: an exception is
    raised eagerly BEFORE the iterator exists, this event arrives inside
    one."""

    reason: str


class Failed(Schema):
    """The stream failed; what already streamed is kept and recorded.
    `reason` is the sentence to show."""

    reason: str


class Done(Schema):
    """The stream's end: what the reply came to (None when the stream
    said nothing of itself) and the verbose stats line — the report's
    `text()`, "" unless /set verbose, so a frontend prints it as it is.
    Every row landed before it, each told by its own event. A reply
    that ended on a question is in the reply's own segments, for a
    frontend to pose: the next line played is the answer, and nothing
    more is built around answering."""

    report: ReplyReport | None
    stats: str


class ReplyReport(Report):
    """What the reply came to: the stream's own account (`stats` — the
    spans, the counts, why the model stopped) beside what the stream
    cannot know, the context the prompt was measured against; and what
    follows from both. A frontend draws its stats line from these;
    `text()` is the line the terminal prints. `truncated` is a reply
    the model did not finish — cut at the reply limit, mid-sentence —
    and `notice` the sentence that says so, "" otherwise."""

    stats: Stats
    context_in_force: int | None  # what the prompt was cut to: the window, under the limit

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
        if self.stats.prompt_tokens is None or not self.context_in_force:
            return None
        return round(100 * self.stats.prompt_tokens / self.context_in_force)

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
            parts.append(
                f"actual ctx {self.context_used}% / {format_context(self.context_in_force)}"
            )
        return f"[ {', '.join(parts)} ]"


PlayEvent = (
    UserMessage
    | AssistantText
    | AssistantReasoning
    | AssistantToolCall
    | AssistantMessage
    | ToolMessage
    | Declined
    | Failed
    | Done
)
