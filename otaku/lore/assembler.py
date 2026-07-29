"""The prompt assembler — composes what the model sees each turn.

The shape is deliberately story-like, because injected structure breaks a
roleplay model's prose. The system message is the user's own text,
untouched. Then the transcript as HEAD + RECAP + TAIL:

- the first `head_messages` verbatim (the opening carries the story's
  voice),
- the closed scenes between head and tail as their summaries, in order,
  rendered as one recap interlude opened by `recap_header` — prose and
  nothing else; journals stay in the store, never in the prompt,
- the tail verbatim, aligned to a scene boundary: it starts right after
  the last summarized scene's end, targeting the most recent
  `tail_messages`.

The recap is capped at `_RECAP_FRACTION` of the budget: beyond it the
oldest summaries drop out and the story-so-far rollup takes their place at
the front, so the story never outgrows its own recap. A short story — or
one with no covering scene — goes out all-verbatim.

Message bodies go out exactly as stored — never rewritten, no `Name:`
prefixes, no turn-taking guards: prose carries its own attribution, and
the wire promise is that the code adds NOTHING but the recap (`/context`
and the request log show it holding). The `/me`, `/you`, and `/ooc`
directions live in a turn's `framing` column and are joined to its body
(`formatting.combine_framing`) only at wire time.

The wire unit is the exchange: consecutive same-role rows (a `/me`
direction beside its line, the recap beside the tail) merge into one turn,
blank-line separated, so the wire alternates the way a chat API expects.
Roles are fixed as stored — nothing relabels them.

Token counts are estimated at ~4 chars/token — close enough for budgeting
without a tokenizer dependency. When the window still overflows, the
tail's oldest messages drop first, and the cut is snapped to a
`_TRIM_BLOCK` boundary so the same message starts the tail for many turns
in a row instead of sliding every turn (a rolling cut would invalidate the
server's prompt cache on every request).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from otaku.formatting import combine_framing
from otaku.store import Store
from otaku.store.schema import Message, Scene

_DEFAULT_CONTEXT = 8_192  # when the backend doesn't expose the loaded window
_RESPONSE_RESERVE = 1_024  # tokens left for the model's reply
_MIN_KEEP = 2  # never trim the transcript below this many messages
_TRIM_SLACK = 0.1  # over-budget trims aim this far below budget (headroom)
# The trim's cut is snapped to a multiple of this, so the tail starts at
# the same message for this many turns instead of sliding every turn.
_TRIM_BLOCK = 10
# The recap may take at most this share of the budget. Beyond it, the
# OLDEST scene summaries drop out and the story-so-far rollup is prepended
# in their place.
_RECAP_FRACTION = 0.25


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class AssembledPrompt:
    """The wire-ready request plus the numbers behind it."""

    messages: list[Message]  # [system?] + the wire turns
    context_max: int
    system_tokens: int
    transcript_tokens: int  # head + recap + tail estimate
    head_count: int  # verbatim opening messages on the wire
    scenes_summarized: int  # scene summaries standing in for the middle
    recap: str  # the recap text, "" when none (render_preview needs it)
    transcript_kept: int  # verbatim messages on the wire (head + tail)
    transcript_total: int

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.transcript_tokens


class StoryView(Protocol):
    """What `assemble_story` reads off a session or a worker job: the
    story, its transcript, and the shaping settings."""

    @property
    def story_id(self) -> int | None: ...
    @property
    def system(self) -> str: ...
    @property
    def messages(self) -> list[Message]: ...
    @property
    def recap_header(self) -> str: ...
    @property
    def head_messages(self) -> int: ...
    @property
    def tail_messages(self) -> int: ...


def assemble_story(store: Store, view: StoryView, context_max: int | None) -> AssembledPrompt:
    """`assemble` over the story's current scenes — the one wrapper every
    call site (the turn, /context, the warm-up) goes through, so none can
    disagree on what the next request looks like."""
    scenes = _current_scenes(store, view.story_id, view.messages)
    return assemble(
        view.system,
        view.messages,
        context_max,
        scenes=scenes,
        recap_header=view.recap_header,
        head_messages=view.head_messages,
        tail_messages=view.tail_messages,
    )


def assemble(
    system: str,
    messages: list[Message],
    context_max: int | None,
    *,
    scenes: Sequence[Scene] = (),
    recap_header: str = "",
    head_messages: int = 20,
    tail_messages: int = 150,
) -> AssembledPrompt:
    """Compose the next request from the session's transcript and the
    story's current scenes (see `current_scenes`). With no covering scene —
    or a transcript short enough — this degrades to the raw prompt a plain
    chat would send. `recap_header`, when given, opens the recap block; it
    is sent, so `/context` needs no heading of its own."""
    window = context_max or _DEFAULT_CONTEXT
    system_tokens = estimate_tokens(system) if system else 0
    budget = max(0, window - _RESPONSE_RESERVE - system_tokens)

    head, summaries, tail = _split_transcript(messages, scenes, head_messages, tail_messages)
    recap, kept_summaries = _compose_recap(summaries, scenes, recap_header, budget)

    used = (
        sum(_wire_tokens(m) for m in head)
        + (estimate_tokens(recap) if recap else 0)
        + sum(_wire_tokens(m) for m in tail)
    )
    if used > budget:
        used, tail = _trim_tail(used, head, tail, budget)

    shaped: list[Message] = list(head)
    if recap:
        shaped.append(Message(role="user", body=recap))
    shaped.extend(tail)

    wire: list[Message] = []
    if system:
        wire.append(Message(role="system", body=system))
    wire.extend(_wire_turns(shaped))
    return AssembledPrompt(
        messages=wire,
        context_max=window,
        system_tokens=system_tokens,
        transcript_tokens=used,
        head_count=len(head),
        scenes_summarized=kept_summaries,
        recap=recap,
        transcript_kept=len(head) + len(tail),
        transcript_total=len(messages),
    )


def render_preview(prompt: AssembledPrompt, *, dim: str = "", reset: str = "") -> str:
    """The `/context` view: the request EXACTLY as it will be sent. Nothing
    here is otaku's own text except the dim `[role]` markers (standing for
    the JSON role field) and the token summary above — every other line is
    content the model receives, in order."""
    lines = ["Context preview — the exact request to be sent. Context summary:", ""]
    used = round(100 * prompt.total_tokens / prompt.context_max) if prompt.context_max else 0
    lines.append(
        f"  ~{prompt.total_tokens:,} tokens · {used}% of the {prompt.context_max:,} window"
    )
    if prompt.system_tokens:
        lines.append(f"  system {prompt.system_tokens:,} · transcript {prompt.transcript_tokens:,}")
    # The summaries are not a third slice of the transcript: they STAND IN
    # for the messages between head and tail. Naming that count is what
    # makes the line add up to the story's length instead of to nothing.
    tail = prompt.transcript_kept - prompt.head_count
    middle = prompt.transcript_total - prompt.transcript_kept
    if prompt.scenes_summarized:
        lines.append(
            f"  {prompt.head_count} head + {tail} tail verbatim, plus {middle} middle "
            f"inserted in between as {prompt.scenes_summarized} scene summaries"
        )
    else:
        lines.append(f"  {prompt.transcript_kept} messages verbatim")

    for message in prompt.messages:
        lines.append("")
        lines.append(f"{dim}[{message.role}]{reset}")
        lines.extend(_preview_body(message.body, prompt.recap))
    return "\n".join(lines)


# ---------- assembly internals ----------


def _current_scenes(store: Store, story_id: int | None, messages: list[Message]) -> list[Scene]:
    if story_id is None or not messages:
        return []
    return store.scenes.get_current(story_id, [m.id for m in messages])


def _split_transcript(
    messages: list[Message],
    scenes: Sequence[Scene],
    head_messages: int,
    tail_messages: int,
) -> tuple[list[Message], list[str], list[Message]]:
    """HEAD + covering scene summaries + TAIL. The tail is scene-aligned:
    it starts right after the last summarized scene's end, targeting the
    most recent `tail_messages`. Scenes ending inside the head or the tail
    stay verbatim there and are not summarized. Falls back to
    everything-verbatim when the transcript is short or no scene summary
    covers the middle."""
    if len(messages) <= head_messages + tail_messages:
        return [], [], messages
    position = {m.id: i for i, m in enumerate(messages)}
    head_end = head_messages - 1
    tail_target = len(messages) - tail_messages
    covered = [
        s
        for s in scenes
        if s.summary and head_end < position.get(s.end_message_id, -1) < tail_target
    ]
    if not covered:
        return [], [], messages
    boundary = position[covered[-1].end_message_id]
    return messages[:head_messages], [s.summary for s in covered], messages[boundary + 1 :]


def _compose_recap(
    summaries: list[str], scenes: Sequence[Scene], recap_header: str, budget: int
) -> tuple[str, int]:
    """The recap block plus how many scene summaries it carries: the
    covering summaries, oldest first — capped at `_RECAP_FRACTION` of the
    budget, the oldest dropping out and the story-so-far rollup (the newest
    scene history) taking their place."""
    if not summaries:
        return "", 0
    recap_budget = int(budget * _RECAP_FRACTION)
    kept = list(summaries)
    used = sum(estimate_tokens(s) for s in kept)
    while len(kept) > 1 and used > recap_budget:
        used -= estimate_tokens(kept.pop(0))
    count = len(kept)
    if count < len(summaries):
        arc = next((s.history for s in reversed(scenes) if s.history), "")
        if arc:
            kept.insert(0, arc)
    parts = [recap_header] if recap_header else []
    return "\n\n".join(parts + kept), count


def _trim_tail(
    used: int, head: list[Message], tail: list[Message], budget: int
) -> tuple[int, list[Message]]:
    """Over budget: drop the tail's oldest messages, aiming `_TRIM_SLACK`
    below budget, then snap the cut FORWARD to a `_TRIM_BLOCK` multiple so
    the boundary stays put across turns — unless the window is so tight the
    block would eat half of what fits, where the cache is a lost cause
    anyway and context wins."""
    target = budget - int(budget * _TRIM_SLACK)
    dropped = 0
    while dropped < len(tail) - 1 and len(head) + len(tail) - dropped > _MIN_KEEP:
        if used <= target:
            break
        used -= _wire_tokens(tail[dropped])
        dropped += 1
    affordable = len(tail) - dropped
    snapped = min(
        -(-dropped // _TRIM_BLOCK) * _TRIM_BLOCK,
        max(0, len(tail) - 1),
        max(0, len(head) + len(tail) - _MIN_KEEP),
    )
    if len(tail) - snapped >= max(_MIN_KEEP, affordable // 2):
        used -= sum(_wire_tokens(m) for m in tail[dropped:snapped])
        dropped = snapped
    return used, tail[dropped:]


def _wire_tokens(message: Message) -> int:
    return estimate_tokens(combine_framing(message.body, message.framing))


def _wire_turns(kept: list[Message]) -> list[Message]:
    """Transcript rows → wire turns: each turn's own text and nothing else.
    Consecutive same-role rows rejoin into one turn — storage granularity is
    otaku's bookkeeping; the model sees one prompt per exchange."""
    out: list[Message] = []
    for message in kept:
        text = combine_framing(message.body, message.framing)
        if out and out[-1].role == message.role:
            out[-1] = Message(role=message.role, body=out[-1].body + "\n\n" + text)
        else:
            out.append(Message(role=message.role, body=text))
    return out


def _preview_body(text: str, recap: str) -> list[str]:
    """Content lines for the preview. Blank lines are dropped to keep it
    tight, EXCEPT in the turn carrying the recap, where paragraph breaks
    are load-bearing: they separate one scene summary from the next (and
    the last summary from any message text merged in after it)."""
    if recap and recap in text:
        out: list[str] = []
        for line in text.splitlines():
            if line.strip():
                out.append(line)
            elif out and out[-1] != "":
                out.append("")  # collapse runs, keep one
        return out
    return [line for line in text.splitlines() if line.strip()]
