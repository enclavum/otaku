"""Injections: text the context carries besides the story.

Never stored, never seen by the lore pass, shown by `/context` because
it is on the wire. This module owns what one IS and where it GOES:
appended to the system message (`inject_into_system`), or given a row
of its own before one of the reader's messages (`inject_into_tail`) — a
synthesized user row, wire-only as a recap row is, which the
assembler's one merge then joins to its neighbour. Who makes one is
above this package (a story's settings, `backend.story`; later a
lorebook), and the assembler is handed them built. WHEN they go in, and
what they cost the budget, is the assembler's.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from otaku.store.schema import InjectionPosition, Message

# The kind of an injected row: wire-only, its body already wire text.
ROW_KIND = "injection"


@dataclass(frozen=True)
class Injection:
    """Text sent as it is; an empty one is not sent. At the system
    position it is appended to the system message, after the premise.
    At a depth it is a user row of its own, placed BEFORE that one of
    the reader's messages counted from the end — 1 the newest, 2 the
    one before — which the merge then heads with it; never past the
    recap, and never after the newest row, which keeps the last word and
    its cue."""

    owner: str  # what made it — a setting's name, later a lorebook's — for whoever reports it
    text: str
    position: InjectionPosition


def inject_into_system(system: str, injections: Iterable[Injection]) -> str:
    """The system message with every "system" injection after the
    premise, a blank line between; without a premise, the injections
    alone."""
    texts = (i.text for i in injections if i.position.depth is None)
    return "\n\n".join(part for part in (system, *texts) if part)


def inject_into_tail(
    rows: Sequence[Message], injections: Iterable[Injection], wall: int
) -> list[Message]:
    """The rows with each numbered injection's own row (`ROW_KIND`) in
    its place: before the reader's message its position names, counted
    from the end over the rows AS THEY STAND — a position counts the
    reader's rows, never a reply, a recap row or another injection. Never
    above `wall` (where the recap ends): a message the count does not
    reach puts it at the wall. Injections sharing a place keep the order
    given. Only a story with no rows at all sends one on its own."""
    by_place: dict[int, list[Injection]] = {}
    for injection in injections:
        if injection.position.depth is None or not injection.text:
            continue  # the system position is `inject_into_system`'s; an empty one is not sent
        wanted = injection.position.depth
        place = wall
        seen = 0
        for at in range(len(rows) - 1, wall - 1, -1):
            if rows[at].role != "user":
                continue
            seen += 1
            if seen == wanted:
                place = at
                break
        by_place.setdefault(place, []).append(injection)
    placed: list[Message] = []
    for at in range(len(rows) + 1):
        placed += [
            Message(role="user", body=injection.text, kind=ROW_KIND)
            for injection in by_place.get(at, ())
        ]
        placed += rows[at : at + 1]
    return placed
