"""The prompt assembler — composes what the model sees each turn.

`docs/context_design.md` is the spec, and this module is shaped after
its cases so the two read side by side:

- cases 1-2 (a short story; no summaries): everything verbatim —
  `_split_transcript` finds no middle to replace;
- case 3 (scenes cover the middle): HEAD verbatim, the covered scenes
  as their summaries in one recap, the TAIL verbatim and scene-aligned
  — `_split_transcript` again, the recap laid out by `_degrade_levels`
  (its first level);
- case 4 (the context outgrows the limit): the oldest summaries drop
  and the story-so-far THROUGH the last replaced scene stands in for
  them — `_degrade_levels`;
- case 5 (degrading summaries is not enough): the tail target steps
  down `_TAIL_STEP` at a time to the `_TAIL_FLOOR`, the context rebuilt
  at each rung — the ladder loop in `_assemble`;
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
promise: the code adds NOTHING but the recap — bodies go out exactly
as stored, composed per turn by `syntax.to_wire`.
"""

from bisect import bisect_left
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

from otaku.context import syntax
from otaku.context.cards import card_to_wire
from otaku.store import Store
from otaku.store.schema import Message, Scene

_DEFAULT_CONTEXT = 8_192  # when the provider states no max context
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


@dataclass(frozen=True)
class ContextShape:
    """The shaping settings an assembly runs under — read off the session
    or carried by a worker Job, so the two can never disagree."""

    # All required: the values come from the config and the prompts —
    # defaults here would be a second copy of theirs.
    head_messages: int
    min_tail_messages: int  # the tail never targets fewer; case 5 lowers it in emergencies
    max_context_setting: int  # tokens the prompt may use at most; 0 = the model's max context
    recap_header: str
    card_framing: str  # the card block template, for composing card rows


@dataclass(frozen=True)
class WireTurn:
    """One turn as SENT — composed wire text, nothing else. A distinct
    type from the stored `Message` on purpose: a stored row's body is
    the line as typed, a wire turn's body is what the model receives,
    and the boundary between them is type-checked, not remembered.
    (`body`, not `text`, so it satisfies `providers.WireMessage`.)"""

    role: str
    body: str


@dataclass(frozen=True)
class AssembledPrompt:
    """The wire-ready request plus the numbers behind it."""

    messages: list[WireTurn]  # [system?] + the wire turns
    max_context: int  # the model's, or the default where it states none
    limit: int  # what the prompt measured against: min(max_context, setting) - reply reserve
    system_tokens: int
    transcript_tokens: int  # head + recap + tail estimate
    head_count: int  # verbatim opening messages on the wire
    scenes_summarized: int  # scene summaries standing in for the middle
    scenes_rolled_up: int  # scenes the history recap covers instead, 0 without one
    history: str  # the story-so-far on the wire, "" when none (case 4's mark)
    recap: str  # the whole recap text, "" when none (the preview keys on it)
    tail_target: int  # the case-5 rung the tail was built at
    tail_setting: int  # the configured min_tail_messages, for comparison
    transcript_kept: int  # verbatim messages on the wire (head + tail)
    transcript_total: int

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.transcript_tokens


def estimate_tokens(text: str) -> int:
    """~4 chars/token — close enough for budgeting without a tokenizer."""
    return max(1, len(text) // 4)


def assemble_story(
    store: Store,
    story_id: int | None,
    *,
    system: str,
    messages: list[Message],
    shape: ContextShape,
    max_context: int | None,
) -> AssembledPrompt:
    """The next request over the story's CURRENT scenes and card
    archives — the one door every call site (the turn, the preview, the
    warm-up) goes through, so none can disagree on what is sent. Card
    rows compose HERE, from the cast's current archives: the stored row
    is the line as typed, and what it sends follows the TOML wherever a
    lore edit took it. The package's one store read; it writes nothing.
    Raises `ContextOverflowError` (case 6) when even the case-5 floor
    cannot fit."""
    scenes = _current_scenes(store, story_id, messages)
    return _assemble(
        system,
        _composed_cards(store, story_id, messages, shape.card_framing),
        max_context,
        scenes=scenes,
        shape=shape,
    )


# ---------- the cases ----------


def _assemble(
    system: str,
    messages: list[Message],
    max_context: int | None,
    *,
    scenes: Sequence[Scene] = (),
    shape: ContextShape,
) -> AssembledPrompt:
    """The whole algorithm, pure over its inputs (card rows must arrive
    with their bodies already composed): build by cases 1-3, degrade the
    recap (case 4), degrade the tail (case 5) — the first fit wins. The
    shape's `recap_header`, when non-empty, opens the recap block; it is
    sent, so the preview needs no heading of its own. Raises
    `ContextOverflowError` (case 6) when even the case-5 floor cannot
    fit."""
    max_context = max_context or _DEFAULT_CONTEXT
    setting = shape.max_context_setting
    # The context in force: the model's, or the setting where it is lower.
    context = min(max_context, setting) if setting else max_context
    limit = max(0, context - _response_reserve(messages))
    system_tokens = estimate_tokens(system) if system else 0
    budget = max(0, limit - system_tokens)

    for tail_target in _tail_ladder(shape.min_tail_messages):  # case 5
        prompt = _compose(
            system, messages, scenes, shape, tail_target, budget, max_context, limit, system_tokens
        )
        if prompt.transcript_tokens <= budget:
            return prompt
    # Case 6: nothing left to degrade — refuse with directions, and the
    # two figures that did not meet: the smallest the story gets, and
    # the context it had to fit.
    needed = prompt.transcript_tokens + system_tokens
    raise ContextOverflowError(
        f"The story does not fit the context limit: it needs {needed:,} tokens even fully "
        f"summarized and with the tail reduced, and the context is {context:,} tokens — raise "
        f"it or run /extract to close more scenes."
    )


def _response_reserve(messages: list[Message]) -> int:
    """Room left for the model's reply: the longest of the story's last
    `_RESERVE_SAMPLE` assistant replies, `_RESERVE_HEADROOM` on top — a
    terse story reserves little, a florid one enough. Before any reply
    exists to measure, `_RESERVE_FALLBACK` stands in."""
    replies = [m for m in messages if m.role == "assistant"][-_RESERVE_SAMPLE:]
    if not replies:
        return _RESERVE_FALLBACK
    return int(max(estimate_tokens(m.body) for m in replies) * _RESERVE_HEADROOM)


def _tail_ladder(min_tail_messages: int) -> list[int]:
    """Case 5's rungs: the configured tail target first, then
    `_TAIL_STEP` fewer per rung, stopping at the `_TAIL_FLOOR`. The
    reduction never persists — the setting is the caller's."""
    rungs = [min_tail_messages]
    while rungs[-1] > _TAIL_FLOOR:
        rungs.append(max(_TAIL_FLOOR, rungs[-1] - _TAIL_STEP))
    return rungs


def _compose(
    system: str,
    messages: list[Message],
    scenes: Sequence[Scene],
    shape: ContextShape,
    tail_target: int,
    budget: int,
    max_context: int,
    limit: int,
    system_tokens: int,
) -> AssembledPrompt:
    """One rung of the ladder: the context by cases 1-3, then case 4's
    degrade levels until one fits the budget. Returns the deepest level
    when none does — the ladder reads the size and steps down."""
    head, covered, tail = _split_transcript(messages, scenes, shape.head_messages, tail_target)
    head_tokens = sum(_wire_tokens(m) for m in head)
    tail_tokens = sum(_wire_tokens(m, is_last=i == len(tail) - 1) for i, m in enumerate(tail))

    # The doc's case 4 drops summaries until the context fits and only
    # then swaps the history in; here every candidate level carries its
    # history INSIDE the fit test, and one more scene is absorbed when a
    # level still does not fit. A deliberate divergence: the doc conveys
    # the big picture, resting on the shared assumption that a history
    # is about one summary long — this loop is what that picture means
    # once every size is actually measured.
    level: tuple[list[Message], str, int, int] = ([], "", 0, 0)
    recap_tokens = 0
    for level in _degrade_levels(covered, shape.recap_header):
        recap_tokens = estimate_tokens(_recap_text(level[0])) if level[0] else 0
        if head_tokens + recap_tokens + tail_tokens <= budget:
            break  # the first fit wins; the deepest level rides otherwise
    recap_rows, history, summarized, rolled = level
    recap = _recap_text(recap_rows)

    wire: list[WireTurn] = []
    if system:
        wire.append(WireTurn(role="system", body=system))
    wire.extend(_wire_turns(head + recap_rows + tail))
    return AssembledPrompt(
        messages=wire,
        max_context=max_context,
        limit=limit,
        system_tokens=system_tokens,
        transcript_tokens=head_tokens + recap_tokens + tail_tokens,
        head_count=len(head),
        scenes_summarized=summarized,
        scenes_rolled_up=rolled,
        history=history,
        recap=recap,
        tail_target=tail_target,
        tail_setting=shape.min_tail_messages,
        transcript_kept=len(head) + len(tail),
        transcript_total=len(messages),
    )


@dataclass
class _CoveredScene:
    """One scene covering part of the middle, as the recap tells it:
    its summary, the story-so-far rollup THROUGH it (its rung of the
    ladder), and the card rows its span held — paired so case 4 can
    replace a summary without losing what must float."""

    cards: list[Message]
    summary: str
    history: str


def _split_transcript(
    messages: list[Message],
    scenes: Sequence[Scene],
    head_messages: int,
    min_tail_messages: int,
) -> tuple[list[Message], list[_CoveredScene], list[Message]]:
    """Cases 1-3: HEAD + the covered scenes + TAIL. The tail is
    scene-aligned and the setting is a MINIMUM: the tail starts right
    after the last summarized scene's end and never holds fewer than
    `min_tail_messages` — a scene whose span ends exactly at the tail's
    first message is not summarized; its whole span rides verbatim and
    the tail grows past the minimum. Scenes ending inside the head or
    the tail stay verbatim there and are not summarized.

    A card row in the replaced region is never replaced with it: it joins
    the first covered scene ending at or after it — the summary it will
    stand in front of. A card's position deliberately
    does not matter, its retention does. Cases 1-2 (short by count, or
    no covering scene) come out of here as no covered scenes —
    everything verbatim, the head split off all the same."""
    if len(messages) <= head_messages + min_tail_messages:
        return messages[:head_messages], [], messages[head_messages:]
    position = {m.id: i for i, m in enumerate(messages)}
    head_end = head_messages - 1
    # The tail's first message sits min_tail_messages-plus-one from the
    # end; only a scene ending strictly BEFORE it may be summarized.
    tail_start = len(messages) - min_tail_messages - 1
    covering = [
        s
        for s in scenes
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


def _degrade_levels(
    covered: list[_CoveredScene], recap_header: str
) -> Iterator[tuple[list[Message], str, int, int]]:
    """Case 4, one level at a time: level 0 is case 3's recap — every
    covered scene's cards in front of its summary, oldest first. Level k
    replaces the summaries of the first k+1 scenes with the story-so-far
    THROUGH the last of them (its own history rung); their cards float
    to the front, in front of the history. A replaced scene without a
    rung falls back to the nearest older one — the scenes past that rung
    go uncovered rather than retold.

    Yields (recap rows, history text, summaries kept, scenes the history
    covers) — level 0 always, then each deeper level up to the history
    alone. No covered scenes yields the one empty level (cases 1-2)."""
    if not covered:
        yield [], "", 0, 0
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
        if recap_header:
            rows.append(_recap_row(recap_header))
        for scene in replaced:
            rows.extend(scene.cards)
        if history:
            rows.append(_recap_row(history))
        for scene in kept:
            rows.extend(scene.cards)
            rows.append(_recap_row(scene.summary))
        yield rows, history, len(kept), rolled


def _recap_text(rows: list[Message]) -> str:
    """The recap as one text — what `_wire_turns` will make of the rows,
    sized and previewed as such."""
    return "\n\n".join(_wire_text(m) for m in rows)


# ---------- store reads and row composition ----------


def _current_scenes(store: Store, story_id: int | None, messages: list[Message]) -> list[Scene]:
    if story_id is None or not messages:
        return []
    return store.scenes.get_current(story_id, [m.id for m in messages])


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


def _recap_row(text: str) -> Message:
    """A synthesized user row of the recap — marked wire-only, its body
    already wire text (see `_wire_text`)."""
    return Message(role="user", body=text, kind="recap")


def _wire_text(message: Message, *, is_last: bool = False) -> str:
    """One row's wire text. A recap row's body IS its wire text —
    synthesized here, never stored, so it never reaches `syntax.to_wire`;
    every other row (card rows included: their bodies arrive composed,
    and `to_wire`'s card branch returns them untouched — card prose that
    happens to spell an inliner is never split as syntax) composes per
    turn."""
    if message.kind == "recap":
        return message.body
    return syntax.to_wire(message, is_last=is_last)


def _wire_tokens(message: Message, *, is_last: bool = False) -> int:
    """Tokens one row costs on the wire."""
    return estimate_tokens(_wire_text(message, is_last=is_last))


def _wire_turns(kept: list[Message]) -> list[WireTurn]:
    """Transcript rows → wire turns: each turn's own text and nothing else.
    Consecutive same-role rows rejoin into one turn — storage granularity is
    otaku's bookkeeping; the model sees one prompt per exchange."""
    out: list[WireTurn] = []
    for position, message in enumerate(kept):
        # Newest = the LAST POSITION, never object identity: two turns can
        # hold the same text, and only where a row sits decides whether its
        # cue is still live.
        text = _wire_text(message, is_last=position == len(kept) - 1)
        if out and out[-1].role == message.role:
            out[-1] = WireTurn(role=message.role, body=out[-1].body + "\n\n" + text)
        else:
            out.append(WireTurn(role=message.role, body=text))
    return out
