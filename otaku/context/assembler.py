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

The limit is the smaller of the model's max context and the
`max_context` setting (0 = the model's), minus a reserve for the model's
reply — sized
from the story's own recent replies, so a terse story reserves little
and a florid one enough. Card rows are never summarized and never
dropped: a card in a replaced scene floats to the front of the recap,
in front of the history.

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
from typing import Protocol

from otaku.context import syntax, tool_calls
from otaku.context.cards import card_to_wire
from otaku.context.injections import ROW_KIND, Injection, inject_into_system, inject_into_tail
from otaku.providers import PicturesRide
from otaku.store import Store
from otaku.store.files import FileStore
from otaku.store.schema import InjectionPosition, Message, Scene

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
    message is the sentence to show."""


class PromptTexts(Protocol):
    """What this module reads of the prompts object by name. The
    settings' `Prompts` satisfies it structurally — this package may
    not import `settings`."""

    @property
    def recap_header(self) -> str: ...
    @property
    def card_framing(self) -> str: ...  # the card block template, for composing card rows


@dataclass(frozen=True)
class ContextShape:
    """The window an assembly is cut to. All required: the values come
    from the config and the model — defaults here would be a second
    copy of theirs."""

    head_messages: int
    min_tail_messages: int  # the tail never targets fewer; case 5 lowers it in emergencies
    max_context: int  # the context in force (`context_in_force`), or the cap alone until it is


@dataclass(frozen=True)
class WirePicture:
    """One picture as SENT: its bytes and their media type, read from
    the files folder for a verbatim row. Satisfies `providers.WireImage`."""

    data: bytes
    media_type: str


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


@dataclass(frozen=True)
class InjectionSent:
    """One injection as the request carries it: whose (its `owner`, a
    setting's name), where it was asked to go, and what it costs — in
    `system_tokens` at the system position, in `transcript_tokens` at a
    depth."""

    owner: str
    position: InjectionPosition
    tokens: int


@dataclass(frozen=True)
class AssembledPrompt:
    """The wire-ready request plus the numbers behind it."""

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

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.transcript_tokens


def estimate_tokens(text: str) -> int:
    """~4 chars/token — close enough for budgeting without a tokenizer."""
    return max(1, len(text) // 4)


def context_in_force(max_context: int | None, setting: int) -> int:
    """The context a prompt is cut to: the model's own (`max_context`,
    or the default where nobody states one), or the `max_context`
    setting where it is lower — 0 for no setting. The one rule, so the
    turn, the preview and the warm-up cut alike."""
    max_context = max_context or _DEFAULT_CONTEXT
    return min(max_context, setting) if setting else max_context


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
    scenes, the card archives and the pictures are read from the store.
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
        shape=shape,
        files=store.files,
        pictures_ride=pictures_ride,
    )
    limit = max(0, shape.max_context - assembly.reserve)

    for tail_target in assembly.rungs:  # case 5
        prompt = assembly.compose(tail_target, limit)
        if prompt.total_tokens <= limit:
            return prompt
    # Case 6: nothing left to degrade — refuse with directions, and the
    # two figures that did not meet: the smallest the story gets, and
    # the context it had to fit.
    raise ContextOverflowError(
        f"The story does not fit the context limit: it needs {prompt.total_tokens:,} tokens even "
        f"fully summarized and with the tail reduced, and the context is {shape.max_context:,} "
        f"tokens — "
        f"raise it or run /extract to close more scenes."
    )


# ---------- the cases ----------


@dataclass
class _CoveredScene:
    """One scene covering part of the middle, as the recap tells it:
    its summary, the story-so-far rollup THROUGH it (its rung of the
    ladder), and the card rows its span held — paired so case 4 can
    replace a summary without losing what must float."""

    cards: list[Message]
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
        pictures_of, pictures_held = self._pictures(verbatim)
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
            if head_tokens + recap.tokens + tail_tokens + injected_tokens <= budget:
                break  # the first fit wins; the deepest level rides otherwise

        wire: list[WireTurn] = []
        if self.system:
            wire.append(WireTurn(role="system", body=self.system))
        # The recap is a wall: an injection stays in the tail under it.
        wall = len(head) + len(recap.rows) if recap.rows else 0
        rows = inject_into_tail(head + recap.rows + tail, self.injections, wall)
        wire.extend(self._wire_turns(rows, pictures_of))
        # A picture is counted only where its row stays verbatim: a
        # summarized row's is neither sent nor missed, the summary is what
        # remains of the turn. Omitted is what would not load — no vision,
        # a file gone.
        pictures_attached = sum(len(m.attachments) for m in verbatim)
        pictures_sent = sum(len(pictures) for pictures in pictures_of.values())
        return AssembledPrompt(
            messages=wire,
            limit=limit,
            system_tokens=system_tokens,
            transcript_tokens=head_tokens + recap.tokens + tail_tokens + injected_tokens,
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
        )

    def _split(self, tail_target: int) -> tuple[list[Message], list[_CoveredScene], list[Message]]:
        """Cases 1-3: HEAD + the covered scenes + TAIL. The tail is
        scene-aligned and the target is a MINIMUM: the tail starts right
        after the last summarized scene's end and never holds fewer than
        `tail_target` — a scene whose span ends exactly at the tail's
        first message is not summarized; its whole span rides verbatim and
        the tail grows past the minimum. Scenes ending inside the head or
        the tail stay verbatim there and are not summarized.

        A card row in the replaced region is never replaced with it: it joins
        the first covered scene ending at or after it — the summary it will
        stand in front of. A card's position deliberately
        does not matter, its retention does. Cases 1-2 (short by count, or
        no covering scene) come out of here as no covered scenes —
        everything verbatim, the head split off all the same."""
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
        covered = [_CoveredScene(cards=[], summary=s.summary, history=s.history) for s in covering]
        ends = [position[s.end_message_id] for s in covering]
        for m in messages[head_messages : boundary + 1]:
            if m.kind == "card":
                covered[bisect_left(ends, position[m.id])].cards.append(m)
        return messages[:head_messages], covered, messages[boundary + 1 :]

    def _pictures(
        self, verbatim: list[Message]
    ) -> tuple[dict[Message, tuple[WirePicture, ...]], int]:
        """Which pictures ride which verbatim row, read from the folder
        here and only here: every row's — or, where the engine gathers
        every picture onto the latest prompt, the newest pictured row's
        alone, so a follow-up without a picture still reaches the model
        with the picture it is about, and no second row's stacks under
        it — and how many the earlier rows were held back that way. A
        file that is gone is skipped; a model that cannot see reads none.
        A row is its own key: a stored row's id tells it from every other."""
        if self.pictures_ride is PicturesRide.NONE:
            return {}, 0
        pictured = [m for m in verbatim if m.attachments]
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
        covered scene's cards in front of its summary, oldest first. Level k
        replaces the summaries of the first k+1 scenes with the story-so-far
        THROUGH the last of them (its own history rung); their cards float
        to the front, in front of the history. A replaced scene without a
        rung falls back to the nearest older one — the scenes past that rung
        go uncovered rather than retold.

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
                rows.extend(scene.cards)
            if history:
                rows.append(self._recap_row(history))
            for scene in kept:
                rows.extend(scene.cards)
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
        prompt per exchange."""
        out: list[WireTurn] = []
        volatile = False  # from the first injected row on: it moves with the end
        for position, message in enumerate(rows):
            # Newest = the LAST POSITION, never object identity: two turns can
            # hold the same text, and only where a row sits decides whether its
            # cue is still live. An injected row never sits there.
            text = self._wire_text(message, is_last=position == len(rows) - 1)
            images = pictures_of.get(message, ())
            volatile = volatile or message.kind == ROW_KIND
            if out and out[-1].role == message.role:
                out[-1] = WireTurn(
                    role=message.role,
                    body=out[-1].body + "\n\n" + text,
                    images=out[-1].images + images,
                    volatile=volatile,
                )
            else:
                out.append(WireTurn(role=message.role, body=text, images=images, volatile=volatile))
        return out


# ---------- store reads ----------


def _composed_cards(
    store: Store, story_id: int | None, messages: list[Message], card_framing: str
) -> list[Message]:
    """The transcript with each card row's wire text composed from its
    character's archive (`cards.card_to_wire`), found through the row's
    speaker link. A row with no reachable archive — the body predates the
    typed-row shape, or the link is gone — sends its body as it stands."""
    if story_id is None or all(m.kind != "card" for m in messages):
        return messages
    archives = {c.id: c.card for c in store.characters.list(story_id) if c.card}
    out: list[Message] = []
    for m in messages:
        toml = archives.get(m.speaker_id) if m.kind == "card" and m.speaker_id else None
        out.append(replace(m, body=card_to_wire(toml, card_framing)) if toml else m)
    return out
