"""A story's messages as a frontend reaches them: listed, read and
corrected (`MessagesResource`), and played — a line sent, the reply that
streams back. A fresh take on the last reply and the trailing exchange
taken back are actions on the story (`api.story.stories`: the regens,
the undos); the regens stream the events of this module.

The stream is the frontend's to drive: iterate to render, close to
cancel. Its events are this module's own, in the order play happens: a
row that landed is a `*Message` — `UserMessage`, `AssistantMessage`,
`ToolMessage` — named by the row's role and carrying the row as a
client draws it; a piece of the reply as it streams is `AssistantText`,
`AssistantReasoning` or `AssistantToolCall`; and exactly one of
`Declined`, `Failed` and `Done` ends the stream. The engine is the
domain's (`domain.story.messages`), with events of its own that this
module tells in these (`as_events`). Closing mid-stream keeps and
records what arrived — Ctrl+C and Ctrl+R are the frontend closing the
generator; a Ctrl+R then simply asks the regens for the fresh take.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator, Sequence
from dataclasses import dataclass

from otaku.backend.api._writes import Changed
from otaku.backend.domain.app import App
from otaku.backend.domain.story import messages as domain_messages
from otaku.backend.domain.story.messages import Actor

# Re-exported: what a picture is handed in as.
from otaku.backend.domain.story.pictures import RawFile as RawFile
from otaku.context.assembler import JoinType
from otaku.formatting import format_context
from otaku.providers import Stats
from otaku.store import schema
from otaku.store.schema import Attachment, StoryMode


class MessagesResource:
    """/stories/{story}/messages — a story's current chain, message by
    message; a line sent plays the story."""

    def __init__(self, app: App) -> None:
        self._app = app

    def list(self, story_id: int, *, last: int | None = None) -> list[Message]:
        """A story's current chain, root → head, as messages — its `last`
        messages alone where a count is given, each joined to the one before
        it all the same. Raises NotFound for a story that is not
        there."""
        drawn = _as_messages(domain_messages.list_messages(self._app, story_id))
        return drawn[-last:] if last else drawn

    def get(self, story_id: int, message_id: int) -> Message:
        """One message of a story's current chain. Raises NotFound for one
        that is not on it."""
        domain_messages.get_message(self._app, story_id, message_id)  # on the chain, or NotFound
        return next(found for found in self.list(story_id) if found.id == message_id)

    def update(self, story_id: int, message_id: int, *, body: str) -> Changed[Message]:
        """The author's correction of one message of a story, on its current
        chain; the whitespace at its edges stays the stored one — a
        correction changes the words, never the join. Raises Invalid on
        an empty body, NotFound for a message that is not on the story's
        chain."""
        said = domain_messages.update_message(self._app, story_id, message_id, body=body)
        return Changed(self.get(story_id, message_id), said)

    def create(
        self,
        story_id: int,
        *,
        line: str,
        files: Sequence[RawFile] = (),
        expected_head: int | None,
    ) -> Iterator[PlayEvent]:
        """One line played on a story (prose or a direction — never a
        command; the frontend routed those already), with `files` the
        pictures the reader attached to it, played on from
        `expected_head`, the head the caller read — in the canvas an empty
        line is the continue. Validation is EAGER: invalid syntax, a
        picture refused, a story that is not there and a head standing
        elsewhere raise Refused before the iterator is returned and
        before anything is recorded. Then the line records and is told
        (UserMessage), and the reply streams as AssistantReasoning /
        AssistantText / AssistantToolCall pieces and lands as
        AssistantMessage, Failed on a stream error, Done at the end. With
        no model selected the line is still recorded — it is story — and
        Declined ends the stream after UserMessage. Closing the generator
        mid-stream records the partial reply and its usage."""
        return as_events(
            domain_messages.submit_line(
                self._app, story_id, line, files, expected_head=expected_head
            )
        )


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


def _as_messages(rows: Sequence[schema.Message]) -> list[Message]:
    """A chain's rows as messages, root → head, each joined to the one
    before it."""
    return [
        _as_message(
            row,
            rows[index - 1] if index else None,
            rows[domain_messages.unit_start(rows, index + 1)].id,
        )
        for index, row in enumerate(rows)
    ]


@dataclass(frozen=True)
class Message:
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


@dataclass(frozen=True)
class UserMessage:
    """The user's row landed (autocorrect settled first) — echo it.
    `note` is the record's own line to show dim beside the echo — the
    dice a /roll rolled; "" for every other row."""

    message: Message
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
    fence ended; the tool's `label` and who acts on its call — "" and
    None for a tool this build does not have — and what a status row
    says while the call is made (`status`)."""

    name: str
    text: str
    closed: bool
    label: str
    actor: Actor | None
    status: str


@dataclass(frozen=True)
class AssistantMessage:
    """The model's row landed, as recorded, once its pieces streamed:
    it ends where the stream ended — at a call of a tool the user
    answers or otaku runs, the closing fence there whether or not the
    model wrote one, or at the reply's own end — and it is told before
    a tool runs, at the end, and when the stream failed alike — the
    message carrying how it joins the text before it, for the canvas."""

    message: Message


@dataclass(frozen=True)
class ToolMessage:
    """A tool ran and its row landed: the result its body, the tool's
    name its `provider`, its `tool_label` what a reader calls it. The
    reply that continues from it streams next."""

    message: Message


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
    """The stream's end: what the reply came to (None when the stream
    said nothing of itself) and the verbose stats line — the report's
    `text()`, "" unless /set verbose, so a frontend prints it as it is.
    Every row landed before it, each told by its own event. A reply
    that ended on a question is in the reply's own segments, for a
    frontend to pose: the next line played is the answer, and nothing
    more is built around answering."""

    report: ReplyReport | None
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
