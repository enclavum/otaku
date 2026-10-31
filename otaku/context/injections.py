"""Injections: text the context carries besides the story.

Never stored, never seen by the lore pass, shown by `/context` because
it is on the wire. This module owns what one IS and where it GOES, by
the position it asked for, whatever the story's length: in the system
message, after the premise (`system_injections`), or placed itself
before one of the reader's messages (`inject_into_tail`) — a piece of
the request of its own, which the chat wire's merge then joins to its
neighbour. Who makes one is its owner's (a story's settings,
`backend.story.settings.services`; a card, `context.cards`, gathered by
`backend.story.request`; later a lorebook), and the assembler is handed
them built. WHEN they go in, and what they cost the budget, is the
assembler's.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from otaku.store.schema import InjectionPosition


@dataclass(frozen=True)
class Injection:
    """Text sent as it is; an empty one is not sent. At the system
    position it is a piece of the system message, after the premise. At
    a depth it is placed BEFORE that one of the reader's messages counted
    from the end — 1 the newest, 2 the one before — which the merge then
    heads with it; never past the recap, and never after the newest row,
    which keeps the last word and its cue."""

    # What made it — a setting's name, a card's, later a lorebook's — for
    # whoever reports it.
    owner: str
    text: str
    position: InjectionPosition


class _Row(Protocol):
    """What a placement counts a row by: whose it is."""

    @property
    def role(self) -> str: ...


_R = TypeVar("_R", bound=_Row)


def system_injections(injections: Iterable[Injection]) -> list[Injection]:
    """The injections that go in the system message, after the premise:
    those at the system position, in the order given; an empty one is
    not sent."""
    return [i for i in injections if i.position.depth is None and i.text]


def inject_into_tail(
    rows: Sequence[_R], injections: Iterable[Injection], wall: int
) -> list[_R | Injection]:
    """The rows — the transcript as the assembler holds it before its
    pieces are made: stored messages, and the summary's own pieces —
    with each numbered injection in its place, itself: before the
    reader's message its position names, counted from the end over the
    rows AS THEY STAND — a position counts the reader's rows, never a
    reply, a recap row or another injection. Never above `wall` (where
    the recap ends): a message the count does not reach puts it at the
    wall. Injections sharing a place keep the order given. Only a story
    with no rows at all sends one on its own."""
    by_place: dict[int, list[Injection]] = {}
    for injection in injections:
        if injection.position.depth is None or not injection.text:
            continue  # the system position is the system message's; an empty one is not sent
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
    placed: list[_R | Injection] = []
    for at in range(len(rows) + 1):
        placed += by_place.get(at, ())
        placed += rows[at : at + 1]
    return placed
