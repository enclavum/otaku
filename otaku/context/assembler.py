"""The prompt assembler — composes what the model sees each turn.

`docs/context_design.md` is the spec, and this module is shaped after
its cases so the two read side by side:

- cases 1-2 (a short story; no summaries): everything verbatim —
  `_Assembly._split` finds no middle to replace;
- case 3 (scenes cover the middle): HEAD verbatim, the covered scenes
  as their summaries in one recap, the TAIL verbatim and scene-aligned
  — `_split` again, the recap laid out by `_Assembly._degrade`
  (its first level);
- case 4 (the context outgrows the limit): the oldest summaries drop
  and the story-so-far THROUGH the last replaced scene stands in for
  them — `_degrade`;
- case 5 (degrading summaries is not enough): the tail target steps
  down `_TAIL_STEP` at a time to the `_TAIL_FLOOR`, the context rebuilt
  at each rung — the ladder loop in `assemble_story`;
- case 6 (nothing left to degrade): the assembly refuses —
  `ContextOverflowError`, the sentence to show riding it.

The limit is the context in force — the model's window, under the
context limit where one is set (`context_in_force`) — minus a reserve for the model's
reply — sized
from the story's own recent replies, so a terse story reserves little
and a florid one enough. Card rows and pinned rows are never
summarized and never dropped: one in a replaced scene rides whole in
front of its scene's summary, in story order, and in front of the
history once its scene is rolled into it.

The shape is deliberately story-like, because injected structure breaks
a roleplay model's prose: the system message is the user's own text,
untouched; the recap is prose and nothing else — journals stay in the
store, never in the prompt. Inside the assembler every part is a row:
the header, the history, and each summary are synthesized user rows
(`kind="recap"` — wire-only, such a row is never stored), so the recap
is joined by the one merge in `_wire_turns` and nothing is hand-glued.
The wire unit is the exchange: consecutive same-role rows (a `/me`
direction beside its line, the recap beside the tail) merge into one
turn, blank-line separated, so the wire alternates the way a chat API
expects; roles are fixed as stored — nothing relabels them. The wire
promise: the code adds NOTHING but the recap and the injections it was
handed — bodies go out exactly as stored, composed per turn by
`syntax.to_wire`.

Injections are `context.injections`' — what one is, how a story's
settings become some, where each goes. This module decides WHEN: the
system ones join the system message before the budget is cut, the
numbered ones take their rows once the recap's level is settled, and
both cost their tokens in the fit.
"""

from bisect import bisect_left
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from otaku.context import syntax, tool_calls
from otaku.context.cards import CARD_PROVIDER, card_to_wire
from otaku.context.injections import ROW_KIND, Injection, inject_into_system, inject_into_tail
from otaku.providers import PicturesRide
from otaku.providers.openai.templates import TextTemplate
from otaku.store import Store
from otaku.store.files import FileStore
from otaku.store.schema import InjectionPosition, Message, Scene, StoryMode

_DEFAULT_CONTEXT = 8_192  # when the provider states no max context
# The kind of a recap row: wire-only, its body already wire text — as
# an injected row is (`injections.ROW_KIND`).
_RECAP_KIND = "recap"
# What one picture costs on the wire, as the budget counts it. Engines
# differ widely (Gemma 3 spends 256 a picture, Qwen2.5-VL about 3,000 at
# the wire size, Llama 3.2 1,600 a tile); the stats line reports the
# real prompt count after every reply, so this is a first estimate.
IMAGE_TOKENS = 1_500
# The reply reserve is sized from the story itself: the longest of the
# last _RESERVE_SAMPLE assistant replies, _RESERVE_HEADROOM on top.
_RESERVE_SAMPLE = 5
_RESERVE_HEADROOM = 1.2
_RESERVE_FALLBACK = 1_024  # before any reply exists to measure
# Case 5's ladder: the tail target steps down by _TAIL_STEP per rung and
# never below _TAIL_FLOOR — below that a roleplay tail loses the voice.
_TAIL_STEP = 50
_TAIL_FLOOR = 50


class ContextOverflowError(Exception):
    """Case 6: the story cannot fit the limit even fully degraded. The
    message is the sentence to show; the figures that did not meet ride
    with it — `tokens` the smallest the story gets, `limit` what it had
    to fit (the context less the reply's reserve), `context_in_force`
    what the request was cut to."""

    def __init__(self, message: str, *, tokens: int, limit: int, context_in_force: int) -> None:
        super().__init__(message)
        self.tokens = tokens
        self.limit = limit
        self.context_in_force = context_in_force


class PromptTexts(Protocol):
    """What this module reads of the prompts object by name. The
    settings' `Prompts` satisfies it structurally — this package may
    not import `settings`."""

    @property
    def recap_header(self) -> str: ...
    @property
    def card_framing(self) -> str: ...  # the card block template, for composing card rows
    @property
    def story_framing(self) -> str: ...  # the text wire's instruction around the premise
    @property
    def tool_framing(self) -> str: ...  # a tool turn's text as the model reads it


@dataclass(frozen=True)
class ContextShape:
    """The window an assembly is cut to. All required: the values come
    from the config and the model — defaults here would be a second
    copy of theirs."""

    head_messages: int
    min_tail_messages: int  # the tail never targets fewer; case 5 lowers it in emergencies
    # What a request is cut to (`context_in_force`) — or, in a job's
    # shape, the limit alone until the warm-up finishes it.
    context_in_force: int


@dataclass(frozen=True)
class WirePicture:
    """One picture as SENT: its bytes and their media type, read from
    the files folder for a verbatim row. Satisfies `providers.WireImage`."""

    data: bytes
    media_type: str


class JoinType(Enum):
    """How a row joins the text before it — the ONE seam vocabulary, on
    the wire turn and on the wire to a frontend (`value`): INLINE, one
    space before it; PARAGRAPH, a blank line. `get_join_type` decides,
    and `join` writes it."""

    INLINE = "inline"
    PARAGRAPH = "paragraph"


@dataclass(frozen=True)
class WireTurn:
    """One turn as SENT — composed wire text and the pictures riding on
    it, nothing else. A distinct type from the stored `Message` on
    purpose: a stored row's body is the line as typed, a wire turn's
    body is what the model receives, and the boundary between them is
    type-checked, not remembered. (`body`, not `text`, so it satisfies
    `providers.WireMessage`.)"""

    role: str
    body: str
    images: tuple[WirePicture, ...] = ()
    # Reads differently next request: an injection that moves with the
    # end landed in it, or above it. A prompt-cache mark belongs before.
    volatile: bool = False
    # How it joins the text before it: its first row's seam (`get_join_type`).
    join_type: JoinType = JoinType.PARAGRAPH


@dataclass(frozen=True)
class InjectionSent:
    """One injection as the request carries it: whose (its `owner`, a
    setting's name or a card's), where it was asked to go, and what it
    costs — in `system_tokens` at the system position, in
    `transcript_tokens` at a depth."""

    owner: str
    position: InjectionPosition
    tokens: int


@dataclass(frozen=True)
class CardSent:
    """One card row as the request carries it: whose (the name it came in
    under) and what it costs, its portrait counted — in
    `transcript_tokens`, whether it rides verbatim or whole in the recap."""

    name: str
    tokens: int


@dataclass(frozen=True)
class AssembledPrompt:
    """The wire-ready request plus the numbers behind it."""

    # The system message's text — the premise and what was injected after
    # it — "" when the request carries none: the chat wire then sends no
    # system row, and the text wire frames the story with its standing
    # instruction alone.
    system: str
    # The same for the TEXT wire: prompts.toml's `story_framing` around the
    # premise, `{premise}` filled — the standing instruction a model turn
    # needs before it (a model turn with no turn before it degenerates,
    # and a new story's premise is empty). Never "": the framing stands.
    text_completion_system: str
    messages: list[WireTurn]  # [system?] + the wire turns

    # The budget.
    limit: int  # what the prompt measured against: the context less the reply reserve
    system_tokens: int  # the premise and what was injected after it
    transcript_tokens: int  # head + recap + tail estimate, the injected rows among them

    # The transcript, split: verbatim, stood in for, verbatim.
    head: int
    middle: int  # what the recap stands in for
    tail: int
    tail_target: int  # the case-5 rung it was built at; below the setting, the limit forced it

    # The recap.
    recap: str  # the whole text, "" when none (the preview keys on it)
    history: bool  # a story-so-far opens it (case 4's mark)
    scenes_summarized: int  # scene summaries standing in for the middle
    scenes_rolled_up: int  # scenes the history covers instead, 0 without one

    # The pictures.
    pictures_sent: int = 0  # riding the verbatim rows, `IMAGE_TOKENS` each in transcript_tokens
    pictures_omitted: int = 0  # attached to verbatim rows but not sent: no vision, or a file gone
    pictures_held: int = (
        0  # on earlier verbatim rows, held back: the engine takes the latest turn's alone
    )

    # The injections, in the order given — what `/context` reports of them.
    injections: tuple[InjectionSent, ...] = ()
    # The card rows, in the order sent — what `/context` reports of them.
    cards: tuple[CardSent, ...] = ()

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.transcript_tokens

    def as_text(self, template: TextTemplate, opener: str = "") -> str:
        """The request as the TEXT wire sends it: `text_completion_system`
        and the story (`as_story_text`, `opener` appended — a retry's
        paragraph break) rendered in `template`'s turn markers, the story
        as the model's own turn in progress. The one rendering: the turn
        sends it and `/context` previews it."""
        return template.render(self.text_completion_system, self.as_story_text() + opener)

    def as_story_text(self) -> str:
        """The story as ONE TEXT, for the text wire: the turns' bodies in
        order, the system's aside; no roles, no labels, each seam
        `get_join_type`'s (`join`). The text keeps the last turn's trailing
        break, a paragraph the reader opened for the model to write in.
        The pictures do not ride it (the caller holds them:
        `pictures_held`)."""
        out = ""
        for turn in self.messages:
            if turn.role == "system":
                continue
            out = join(out, turn.join_type, turn.body) if out else turn.body.lstrip()
        return out


def get_join_type(previous: Message | None, next: Message) -> JoinType:
    """How `next` joins `previous`, the row before it — the wire's and
    the canvas's one rule, read off the two rows and the newlines at the
    boundary: INLINE where both were written in story mode, neither
    stands alone (a card's line or greeting, a chapter), and no newline
    stands at the end of the one or the start of the other; PARAGRAPH
    otherwise — the first row, a chat row or a row standing alone on
    either side, a break the reader typed or the model wrote at the
    edge. Spaces at the edges decide nothing. A tool's result runs on in
    story mode like any row written there: story mode does not draw it,
    so the reply after it goes on in the paragraph the call interrupted."""
    if previous is None:
        return JoinType.PARAGRAPH
    rows = (previous, next)
    if any(row.mode is not StoryMode.STORY for row in rows):
        return JoinType.PARAGRAPH
    # A card's line and greeting stand alone in any mode: nobody wrote
    # them into the text. So does a chapter, a heading over the text.
    if any(row.kind in ("card", "chapter") or row.provider == CARD_PROVIDER for row in rows):
        return JoinType.PARAGRAPH
    if previous.body.rstrip(" \t").endswith("\n") or next.body.lstrip(" \t").startswith("\n"):
        return JoinType.PARAGRAPH
    return JoinType.INLINE


def join(previous: str, join_type: JoinType, next: str) -> str:
    """`next` after `previous` across a seam `join_type` decided: one
    space inline, a blank line for a paragraph — the whitespace at the
    two edges given up for it, so the space is never stored and a word
    is never completed across rows."""
    return previous.rstrip() + (" " if join_type is JoinType.INLINE else "\n\n") + next.lstrip()


def estimate_tokens(text: str) -> int:
    """~4 chars/token — close enough for budgeting without a tokenizer."""
    return max(1, len(text) // 4)


def context_in_force(context_window: int | None, context_limit: int) -> int:
    """The context a prompt is cut to: the model's window (or the
    default where nobody states one), or the context limit where it is
    lower — 0 for no limit. The one rule, so the turn, the preview and
    the warm-up cut alike."""
    window = context_window or _DEFAULT_CONTEXT
    return min(window, context_limit) if context_limit else window


def assemble_story(
    store: Store,
    story_id: int | None,
    *,
    system: str,
    messages: list[Message],
    injections: Sequence[Injection],
    tool_set: tool_calls.ToolSet,
    prompts: PromptTexts,
    shape: ContextShape,
    pictures_ride: PicturesRide = PicturesRide.NONE,
) -> AssembledPrompt:
    """The next request — the one door of the turn, the preview and the
    warm-up. What the caller holds is handed in (a story may not exist
    yet, a warm-up works from a snapshot), the injections built; the
    scenes, the card profiles and the pictures are read from the store.
    Raises `ContextOverflowError` (case 6) when even the case-5 floor
    cannot fit."""
    scenes: list[Scene] = []
    if story_id is not None and messages:
        scenes = store.scenes.get_current(story_id, [m.id for m in messages])
    assembly = _Assembly(
        system=inject_into_system(system, injections),
        messages=_composed_cards(store, story_id, messages, prompts.card_framing),
        scenes=scenes,
        injections=injections,
        tool_set=tool_set,
        recap_header=prompts.recap_header,
        story_framing=prompts.story_framing,
        tool_framing=prompts.tool_framing,
        shape=shape,
        files=store.files,
        pictures_ride=pictures_ride,
    )
    limit = max(0, shape.context_in_force - assembly.reserve)

    for tail_target in assembly.rungs:  # case 5
        prompt = assembly.compose(tail_target, limit)
        if prompt.total_tokens <= limit:
            return prompt
    # Case 6: nothing left to degrade — refuse with directions, and the
    # two figures that did not meet: the smallest the story gets, and
    # the context it had to fit.
    raise ContextOverflowError(
        f"The story does not fit the context limit: it needs {prompt.total_tokens:,} tokens even "
        f"fully summarized and with the tail reduced, and the context is "
        f"{shape.context_in_force:,} tokens — "
        f"raise it or run /extract to close more scenes.",
        tokens=prompt.total_tokens,
        limit=limit,
        context_in_force=shape.context_in_force,
    )


# ---------- the cases ----------


@dataclass
class _CoveredScene:
    """One scene covering part of the middle, as the recap tells it:
    its summary, the story-so-far rollup THROUGH it (its rung of the
    ladder), and the rows its span held that ride whole — its cards and
    pinned rows, in story order — paired so case 4 can replace a summary
    without losing what must float."""

    whole: list[Message]
    summary: str
    history: str


@dataclass(frozen=True)
class _RecapLevel:
    """One of case 4's levels: the recap as rows, and what it tells."""

    rows: list[Message]
    text: str  # the rows as one text — what `_wire_turns` makes of them, sized and previewed
    tokens: int
    history: str  # the story-so-far in it, "" when none
    summaries: int  # scene summaries kept
    rolled_up: int  # scenes the history covers instead


@dataclass(frozen=True)
class _Assembly:
    """What every rung of the ladder composes from — gathered once, the
    store read and the system message injected, and fixed from there."""

    system: str
    messages: list[Message]  # card rows composed
    scenes: Sequence[Scene]
    injections: Sequence[Injection]
    tool_set: tool_calls.ToolSet  # how a row's tool calls go on the wire
    recap_header: str
    story_framing: str  # the text wire's instruction, `{premise}` to fill
    tool_framing: str  # a tool turn's enclosure, `{tool}` and `{body}` to fill
    shape: ContextShape
    files: FileStore  # where a row's pictures are read from
    pictures_ride: PicturesRide

    @property
    def reserve(self) -> int:
        """Room left for the model's reply: the longest of the story's last
        `_RESERVE_SAMPLE` assistant replies, `_RESERVE_HEADROOM` on top — a
        terse story reserves little, a florid one enough. Before any reply
        exists to measure, `_RESERVE_FALLBACK` stands in."""
        replies = [m for m in self.messages if m.role == "assistant"][-_RESERVE_SAMPLE:]
        if not replies:
            return _RESERVE_FALLBACK
        return int(max(estimate_tokens(m.body) for m in replies) * _RESERVE_HEADROOM)

    @property
    def rungs(self) -> list[int]:
        """Case 5's rungs: the configured tail target first, then
        `_TAIL_STEP` fewer per rung, stopping at the `_TAIL_FLOOR`. The
        reduction never persists — the setting is the caller's."""
        rungs = [self.shape.min_tail_messages]
        while rungs[-1] > _TAIL_FLOOR:
            rungs.append(max(_TAIL_FLOOR, rungs[-1] - _TAIL_STEP))
        return rungs

    def compose(self, tail_target: int, limit: int) -> AssembledPrompt:
        """One rung of the ladder: the context by cases 1-3, then case 4's
        degrade levels until one fits the budget — the limit less the
        system message. Returns the deepest level when none does — the
        ladder reads the size and steps down."""
        system_tokens = estimate_tokens(self.system) if self.system else 0
        budget = max(0, limit - system_tokens)
        head, covered, tail = self._split(tail_target)
        verbatim = head + tail
        # A card's or a pinned row in a covered scene rides the recap
        # whole, at every level, and its pictures with it: a card's
        # portrait outlives the summary of the scene it came in.
        whole = [m for scene in covered for m in scene.whole]
        pictures_of, pictures_held = self._pictures(head + whole + tail)
        # The recap's estimate holds their text; their pictures are added here.
        whole_pictures_tokens = IMAGE_TOKENS * sum(len(pictures_of.get(m, ())) for m in whole)
        head_tokens = sum(self._wire_tokens(m, pictures_of.get(m, ())) for m in head)
        tail_tokens = sum(
            self._wire_tokens(m, pictures_of.get(m, ()), is_last=i == len(tail) - 1)
            for i, m in enumerate(tail)
        )
        # What the numbered injections cost; the system ones are in `system`.
        injected_tokens = sum(
            estimate_tokens(i.text)
            for i in self.injections
            if i.position.depth is not None and i.text
        )

        # The doc's case 4 drops summaries until the context fits and only
        # then swaps the history in; here every candidate level carries its
        # history INSIDE the fit test, and one more scene is absorbed when a
        # level still does not fit. A deliberate divergence: the doc conveys
        # the big picture, resting on the shared assumption that a history
        # is about one summary long — this loop is what that picture means
        # once every size is actually measured. Level 0 is always yielded.
        for recap in self._degrade(covered):
            if (
                head_tokens + recap.tokens + whole_pictures_tokens + tail_tokens + injected_tokens
                <= budget
            ):
                break  # the first fit wins; the deepest level rides otherwise

        wire: list[WireTurn] = []
        if self.system:
            wire.append(WireTurn(role="system", body=self.system))
        # The recap is a wall: an injection stays in the tail under it.
        wall = len(head) + len(recap.rows) if recap.rows else 0
        rows = inject_into_tail(head + recap.rows + tail, self.injections, wall)
        wire.extend(self._wire_turns(rows, pictures_of))
        # A picture is counted only where its row is sent whole — verbatim,
        # or riding the recap whole: a summarized row's is neither sent
        # nor missed, the summary is what remains of the turn. Omitted is
        # what would not load — no vision, a file gone.
        pictures_attached = sum(len(m.attachments) for m in head + whole + tail)
        pictures_sent = sum(len(pictures) for pictures in pictures_of.values())
        return AssembledPrompt(
            messages=wire,
            system=self.system,
            text_completion_system=self.story_framing.replace("{premise}", self.system).strip(),
            limit=limit,
            system_tokens=system_tokens,
            transcript_tokens=head_tokens
            + recap.tokens
            + whole_pictures_tokens
            + tail_tokens
            + injected_tokens,
            head=len(head),
            middle=len(self.messages) - len(verbatim),
            tail=len(tail),
            tail_target=tail_target,
            recap=recap.text,
            history=bool(recap.history),
            scenes_summarized=recap.summaries,
            scenes_rolled_up=recap.rolled_up,
            pictures_sent=pictures_sent,
            pictures_omitted=pictures_attached - pictures_sent - pictures_held,
            pictures_held=pictures_held,
            injections=tuple(
                InjectionSent(i.owner, i.position, estimate_tokens(i.text))
                for i in self.injections
                if i.text
            ),
            cards=tuple(
                CardSent(m.speaker or "", self._wire_tokens(m, pictures_of.get(m, ())))
                for m in [*head, *whole, *tail]
                if m.kind == "card"
            ),
        )

    def _split(self, tail_target: int) -> tuple[list[Message], list[_CoveredScene], list[Message]]:
        """Cases 1-3: HEAD + the covered scenes + TAIL. The tail is
        scene-aligned and the target is a MINIMUM: the tail starts right
        after the last summarized scene's end and never holds fewer than
        `tail_target` — a scene whose span ends exactly at the tail's
        first message is not summarized; its whole span rides verbatim and
        the tail grows past the minimum. Scenes ending inside the head or
        the tail stay verbatim there and are not summarized.

        A card's row or a pinned row in the replaced region is never
        replaced with it: it joins the first covered scene ending at or
        after it — the summary it will stand in front of, in story order.
        Cases 1-2 (short by count, or no covering scene) come out of here
        as no covered scenes — everything verbatim, the head split off all
        the same."""
        messages, head_messages = self.messages, self.shape.head_messages
        if len(messages) <= head_messages + tail_target:
            return messages[:head_messages], [], messages[head_messages:]
        position = {m.id: i for i, m in enumerate(messages)}
        head_end = head_messages - 1
        # The tail's first message sits tail_target-plus-one from the end;
        # only a scene ending strictly BEFORE it may be summarized.
        tail_start = len(messages) - tail_target - 1
        covering = [
            s
            for s in self.scenes
            if s.summary and head_end < position.get(s.end_message_id, -1) < tail_start
        ]
        if not covering:
            return messages[:head_messages], [], messages[head_messages:]
        boundary = position[covering[-1].end_message_id]
        covered = [_CoveredScene(whole=[], summary=s.summary, history=s.history) for s in covering]
        ends = [position[s.end_message_id] for s in covering]
        for m in messages[head_messages : boundary + 1]:
            if m.kind == "card" or m.pinned:
                covered[bisect_left(ends, position[m.id])].whole.append(m)
        return messages[:head_messages], covered, messages[boundary + 1 :]

    def _pictures(self, rows: list[Message]) -> tuple[dict[Message, tuple[WirePicture, ...]], int]:
        """Which pictures ride which row sent whole — the verbatim rows and
        the rows the recap carries whole, in story order — read from the
        folder here and only here: every row's — or, where the engine gathers
        every picture onto the latest prompt, the newest pictured row's
        alone, so a follow-up without a picture still reaches the model
        with the picture it is about, and no second row's stacks under
        it — and how many the earlier rows were held back that way. A
        file that is gone is skipped; a model that cannot see reads none.
        A row is its own key: a stored row's id tells it from every other."""
        if self.pictures_ride is PicturesRide.NONE:
            return {}, 0
        pictured = [m for m in rows if m.attachments]
        held = 0
        if self.pictures_ride is PicturesRide.LATEST and pictured:
            held = sum(len(m.attachments) for m in pictured[:-1])
            pictured = pictured[-1:]
        pictures_of: dict[Message, tuple[WirePicture, ...]] = {}
        for message in pictured:
            found = (self.files.get(a.file) for a in message.attachments)
            pictures = tuple(WirePicture(*f) for f in found if f is not None)
            if pictures:
                pictures_of[message] = pictures
        return pictures_of, held

    def _degrade(self, covered: list[_CoveredScene]) -> Iterator[_RecapLevel]:
        """Case 4, one level at a time: level 0 is case 3's recap — every
        covered scene's cards and pinned rows in front of its summary,
        oldest first. Level k replaces the summaries of the first k+1
        scenes with the story-so-far THROUGH the last of them (its own
        history rung); their rows float to the front, in front of the
        history. A replaced scene without a rung falls back to the nearest
        older one — the scenes past that rung go uncovered rather than
        retold.

        Level 0 always, then each deeper level up to the history alone. No
        covered scenes yields the one empty level (cases 1-2)."""
        if not covered:
            yield _RecapLevel([], "", 0, "", 0, 0)
            return
        for level in range(len(covered) + 1):
            replaced, kept = covered[:level], covered[level:]
            history = ""
            rolled = 0
            for index in range(len(replaced) - 1, -1, -1):
                if replaced[index].history:
                    history = replaced[index].history
                    rolled = index + 1
                    break
            rows: list[Message] = []
            if self.recap_header:
                rows.append(self._recap_row(self.recap_header))
            for scene in replaced:
                rows.extend(scene.whole)
            if history:
                rows.append(self._recap_row(history))
            for scene in kept:
                rows.extend(scene.whole)
                rows.append(self._recap_row(scene.summary))
            recap = "\n\n".join(self._wire_text(m) for m in rows)
            yield _RecapLevel(
                rows,
                recap,
                estimate_tokens(recap) if rows else 0,
                history,
                summaries=len(kept),
                rolled_up=rolled,
            )

    # ---- the rows on the wire ----

    @staticmethod
    def _recap_row(text: str) -> Message:
        """A synthesized user row of the recap — marked wire-only, its body
        already wire text (see `_wire_text`)."""
        return Message(role="user", body=text, kind=_RECAP_KIND)

    def _wire_text(self, message: Message, *, is_last: bool = False) -> str:
        """One row's wire text. A recap row's body IS its wire text, and an
        injected row's — synthesized here, never stored, so neither reaches
        `syntax.to_wire`; every other row (card rows included: their bodies
        arrive composed, and `to_wire`'s card branch returns them untouched —
        card prose that happens to spell an inliner is never split as
        syntax) composes per turn, its tool calls first put as the tool
        set has them (`tool_calls.to_wire`)."""
        if message.kind in (_RECAP_KIND, ROW_KIND):
            return message.body
        body = tool_calls.to_wire(message.body, self.tool_set)
        if message.role == "tool":
            # A tool's result — a tool turn: not a typed line, and
            # read out of character, the tool named as the model named
            # it in the call — the row's `provider`.
            name = message.provider or "tool"
            return self.tool_framing.replace("{tool}", name).replace("{body}", body)
        if message.role == "assistant":
            # The model's row is not a typed line — no direction to read
            # off it — and goes as it stands.
            return body
        return syntax.to_wire(replace(message, body=body), is_last=is_last)

    def _wire_tokens(
        self, message: Message, pictures: tuple[WirePicture, ...], *, is_last: bool = False
    ) -> int:
        """Tokens one row costs on the wire: its text, and `IMAGE_TOKENS` for
        each picture to send with it."""
        text_tokens = estimate_tokens(self._wire_text(message, is_last=is_last))
        return text_tokens + IMAGE_TOKENS * len(pictures)

    def _wire_turns(
        self, rows: list[Message], pictures_of: Mapping[Message, tuple[WirePicture, ...]]
    ) -> list[WireTurn]:
        """Transcript rows → wire turns: each turn's own text and pictures
        and nothing else (a recap or injected row has none to look up).
        Consecutive same-role rows rejoin into one turn, pictures and all —
        storage granularity is otaku's bookkeeping; the model sees one
        prompt per exchange. A tool turn goes as the reader's — the chat
        wire has two sides, and a tool's result is what the model is told
        — framed by `_wire_text`. The seam between two rows is
        `get_join_type`'s, written as `as_story_text` writes it (`join`)."""
        # A row with nothing to send — a reply that was only a call of a tool
        # now off — is no turn: an empty one teaches a model to answer with
        # nothing.
        rows = [m for m in rows if self._wire_text(m) or pictures_of.get(m)]
        out: list[WireTurn] = []
        volatile = False  # from the first injected row on: it moves with the end
        for position, message in enumerate(rows):
            # Newest = the LAST POSITION, never object identity: two turns can
            # hold the same text, and only where a row sits decides whether its
            # cue is still live. An injected row never sits there.
            text = self._wire_text(message, is_last=position == len(rows) - 1)
            images = pictures_of.get(message, ())
            volatile = volatile or message.kind == ROW_KIND
            join_type = get_join_type(rows[position - 1] if position else None, message)
            role = "user" if message.role == "tool" else message.role
            if out and out[-1].role == role:
                out[-1] = WireTurn(
                    role=role,
                    body=join(out[-1].body, join_type, text),
                    images=out[-1].images + images,
                    volatile=volatile,
                    join_type=out[-1].join_type,
                )
            else:
                turn = WireTurn(role, text.lstrip(), images, volatile=volatile, join_type=join_type)
                out.append(turn)
        return out


# ---------- store reads ----------


def _composed_cards(
    store: Store, story_id: int | None, messages: list[Message], card_framing: str
) -> list[Message]:
    """The transcript with each card row's wire text composed from its
    character's profile (`cards.card_to_wire`), found through the row's
    speaker link. A row with no reachable profile — the body predates the
    typed-row shape, or the link is gone — sends its body as it stands."""
    if story_id is None or all(m.kind != "card" for m in messages):
        return messages
    carded = {c.id: c for c in store.characters.list(story_id) if c.profile}
    out: list[Message] = []
    for m in messages:
        who = carded.get(m.speaker_id) if m.kind == "card" and m.speaker_id else None
        if who is not None and who.profile:
            m = replace(m, body=card_to_wire(who.profile, card_framing, char=who.name))
        out.append(m)
    return out
