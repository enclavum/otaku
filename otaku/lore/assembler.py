"""The prompt assembler — composes what the model sees each turn.

The system message is the user's own text, untouched. Message bodies go out
exactly as stored — never rewritten, no `Name:` prefixes, no turn-taking
guards: prose carries its own attribution, and the wire promise is that the
code adds NOTHING (`/context` and the request log show it holding). The
`/me`, `/you`, and `/ooc` directions live in a turn's `framing` column and
are joined to its body (`formatting.combine_framing`) only at wire time.

The wire unit is the exchange: consecutive same-role rows (a `/me`
direction beside its line) merge into one turn, blank-line separated, so
the wire alternates the way a chat API expects. Roles are fixed as stored —
nothing relabels them.

Token counts are estimated at ~4 chars/token — close enough for budgeting
without a tokenizer dependency. When the window overflows, the oldest
messages drop first.
"""

from dataclasses import dataclass

from otaku.formatting import combine_framing
from otaku.store.schema import Message

_DEFAULT_CONTEXT = 8_192  # when the backend doesn't expose the loaded window
_RESPONSE_RESERVE = 1_024  # tokens left for the model's reply
_MIN_KEEP = 2  # never trim the transcript below this many messages


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class AssembledPrompt:
    """The wire-ready request plus the numbers behind it."""

    messages: list[Message]  # [system?] + the wire turns
    context_max: int
    system_tokens: int
    transcript_tokens: int
    transcript_kept: int  # messages on the wire after any trim
    transcript_total: int

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.transcript_tokens


def assemble(system: str, messages: list[Message], context_max: int | None) -> AssembledPrompt:
    """Compose the next request from the session's transcript."""
    window = context_max or _DEFAULT_CONTEXT
    system_tokens = estimate_tokens(system) if system else 0
    budget = max(0, window - _RESPONSE_RESERVE - system_tokens)

    kept = list(messages)
    used = sum(estimate_tokens(combine_framing(m.body, m.framing)) for m in kept)
    while len(kept) > _MIN_KEEP and used > budget:
        used -= estimate_tokens(combine_framing(kept[0].body, kept[0].framing))
        kept = kept[1:]

    wire: list[Message] = []
    if system:
        wire.append(Message(role="system", body=system))
    wire.extend(_wire_turns(kept))
    return AssembledPrompt(
        messages=wire,
        context_max=window,
        system_tokens=system_tokens,
        transcript_tokens=used,
        transcript_kept=len(kept),
        transcript_total=len(messages),
    )


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
    if prompt.transcript_kept < prompt.transcript_total:
        dropped = prompt.transcript_total - prompt.transcript_kept
        lines.append(f"  {prompt.transcript_kept} messages verbatim ({dropped} oldest trimmed)")
    else:
        lines.append(f"  {prompt.transcript_kept} messages verbatim")

    for message in prompt.messages:
        lines.append("")
        lines.append(f"{dim}[{message.role}]{reset}")
        lines.extend(line for line in message.body.splitlines() if line.strip())
    return "\n".join(lines)
