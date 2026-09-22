"""Injections: text the context carries besides the story.

Never stored, never seen by the lore pass, shown by `/context` because
it is on the wire. This module owns what one IS and where it GOES:
appended to the system message (`inject_into_system`), or given a row
of its own a fixed place from the end (`inject_into_tail`) — a
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
    """Text sent as it is; an empty one is not sent. At "system" it is
    appended to the system message, after the premise. At a negative
    number it is a user row of its own, placed as `list.insert` would
    place it: -1 above the newest message — which so keeps the last word,
    and its cue the last row's — -3 above the last three; never past the
    recap. A number that is not negative rides as -1."""

    owner: str  # what made it — a setting's name, later a lorebook's — for whoever reports it
    text: str
    position: InjectionPosition = -1


def inject_into_system(system: str, injections: Iterable[Injection]) -> str:
    """The system message with every "system" injection after the
    premise, a blank line between; without a premise, the injections
    alone."""
    texts = (i.text for i in injections if i.position == "system")
    return "\n\n".join(part for part in (system, *texts) if part)


def inject_into_tail(
    rows: Sequence[Message], injections: Iterable[Injection], wall: int
) -> list[Message]:
    """The rows with each numbered injection's own row (`ROW_KIND`) in
    its place. A place is counted from the end of the rows AS THEY STAND
    — a position counts messages, never another injection — never above
    `wall` (where the recap ends), and never after the newest row;
    injections sharing a place keep the order given. Only a story with no
    rows at all sends one on its own."""
    by_place: dict[int, list[Injection]] = {}
    for injection in injections:
        if not isinstance(injection.position, int) or not injection.text:
            continue  # "system" is `inject_into_system`'s; an empty one is not sent
        place = max(wall, len(rows) + min(injection.position, -1))
        by_place.setdefault(place, []).append(injection)
    placed: list[Message] = []
    for at in range(len(rows) + 1):
        placed += [
            Message(role="user", body=injection.text, kind=ROW_KIND)
            for injection in by_place.get(at, ())
        ]
        placed += rows[at : at + 1]
    return placed
