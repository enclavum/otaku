"""What a request carries and what an answer is made of, with no HTTP
in sight: `Ask` — what the path named, what the query asked for, what
the body carried — `Created` for a write that made something, `Blob`
for a read that answers with bytes, `NotFound` for a subject that is
not there; `Pending`, what the web holds between requests on the
session's behalf; and the two shapes a table's row takes, `Route` and
`Flow`. `web.server` takes a request apart into these and carries the
result back."""

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from otaku.backend.api.cards import PreparedCard
from otaku.backend.api.lore import WorkerRun
from otaku.backend.session import Session


@dataclass(frozen=True)
class Ask:
    """One request as a handler sees it, with no HTTP in sight: what the
    PATH named, what the query asked for, and what the body carried.
    `web.server` takes a request apart and hands over these three."""

    params: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    body: Mapping[str, Any] = field(default_factory=dict)

    def id(self, name: str) -> int:
        """One numeric path segment. The router has already matched the
        template, so a value that is not a number is a bug here, not a
        request to refuse."""
        return int(self.params[name])

    def text(self, name: str, default: str = "") -> str:
        """A body field the request may leave out."""
        return str(self.body.get(name, default))

    def need(self, name: str) -> str:
        """A body field the request MUST carry. Missing is a malformed
        request — the server answers 400 — and never a silent default:
        a PATCH with no text would blank what it was meant to correct.
        A field sent as `null` is the same fault wearing a value, and is
        refused for the same reason: coerced, it would store the literal
        title "None"."""
        value = self.body[name]
        if value is None:
            raise TypeError(f"{name} is null")
        return str(value)


@dataclass(frozen=True)
class Created:
    """A write that MADE something: the sentence, and where the thing now
    lives. The server answers `201` and names it in `Location`; anything
    else a write returns is a plain `200`."""

    notice: str
    location: str
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Blob:
    """A read that answers with BYTES rather than JSON — a picture, its
    thumbnail — and the media type to say. A file never changes once
    written, so the server lets a browser keep it: the one exception
    beside the fonts to "nothing is cached"."""

    data: bytes
    media_type: str


def landed_story(session: Session) -> str:
    """Where the story the session just landed in now lives — the
    `Location` a `Created` names for a story made or imported."""
    return f"/api/stories/{session.story_id}"


class NotFound(Exception):  # noqa: N818 — a 404 is an expected answer, not an error
    """The path parsed but the SUBJECT it names is not there — a story id
    the database does not hold, a provider name nothing is configured
    under. The server answers `404`, exactly as it does for a path that
    never matched: the spec draws no line between the two, and a body
    would say nothing the page could show."""


#
# A file arrives, is read and vetted, and only then lands — with a
# question in between. `Pending` keeps what waits between the halves,
# because a request is over before the next one starts.

# How long a card waits on its persona answer before the next prepare
# sweeps it — long enough to read the question, short enough that a
# cancelled import is not still in memory an hour later.
_CARD_PATIENCE = 600.0


class Pending:
    """What the web holds between requests on behalf of the session —
    the state a terminal keeps on its call stack, forced off it here
    because a request ends before the question it opened is answered.

    Two things span requests by design: the extraction passes the page
    polls — keyed by STORY, because an import can start a pass while a
    forced one still runs, and a single slot would deliver one story's
    report as another's — and cards read and vetted, waiting on their
    persona answer, keyed because two tabs preparing at once must not
    swap each other's: the answer would bind the wrong character to the
    wrong persona.

    Touched on the session's one thread (every flow runs there), with
    one exception: `extraction` is also READ from a handler thread by
    the poll, which is safe because a dict get against a dict set is
    atomic under the GIL and a run's `poll` is channel-safe by
    contract."""

    def __init__(self) -> None:
        self.extraction: dict[int, WorkerRun] = {}
        self._cards: dict[str, tuple[PreparedCard, float]] = {}

    def hold_card(self, prepared: PreparedCard) -> str:
        """Keep a prepared card for its persona answer; returns the
        token the page hands back. A persona ask that was cancelled,
        reloaded past, or closed never comes back for its card — nothing
        else would ever drop it, so each new one sweeps what has gone
        stale."""
        now = time.monotonic()
        for token, (_, asked) in list(self._cards.items()):
            if now - asked > _CARD_PATIENCE:
                del self._cards[token]
        token = secrets.token_hex(8)
        self._cards[token] = (prepared, now)
        return token

    def take_card(self, token: str) -> PreparedCard | None:
        """The card a token names, forgotten in the taking — None for a
        page that asked twice, or a reload between the two halves."""
        held = self._cards.pop(token, None)
        return held[0] if held else None


# The two shapes a table's row takes. A route's work: the session, and
# the request taken apart — what it returns is what the page gets, a
# payload, a bare sentence the server wraps as a notice, or a `Created`
# when it made something. A flow's is the same plus the state that
# outlives one request: taking `Pending` is what says so, and a route
# that spans two requests cannot be mistaken for one that does not.
Route = Callable[[Session, Ask], Any]
Flow = Callable[[Session, Ask, Pending], Any]
