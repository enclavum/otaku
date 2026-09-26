"""What the page asks for, as plain data — this frontend's whole surface
over an open session, one table per kind of request.

`ROUTES` is the whole of it: one entry per method and path, each
taking the session and an `Ask` — what the path named, what the query
asked for, what the body carried — and returning something `json` can
write. A bare sentence back is a notice; a `Created` says a thing was
made and where it now lives. `Pending` holds what outlives a request,
and reaches only the rows of `FLOWS`. `play` and `regenerate`
return the reply's event stream and `event` names each event on the
wire. What is NOT here is HTTP: `web.server` matches a request against
this table and carries the result, and nothing else.

The package mirrors `backend.api`: one module per twin — `play`,
`stories`, `lore`, `cards`, `providers`, `settings`, `reports`,
`transfer` — each translating its twin's answers into the page's shapes
and holding its own rows, and `request` for what every row is handed
and hands back. A module calls its own twin and nothing else of
`backend.api`; what it needs of another twin it takes from that twin's
sibling here (`stories.story` takes the lore half of its answer from
`lore.memory`), which is what keeps every translation of one twin in
one module — `tests/test_architecture.py` holds it, `request` the one
exception, importing two twins' TYPES for `Pending`. The tables here
are the modules' rows merged, and a path in two of them is a fault at
import: the server matches one sorted list built from both, and a
duplicate would let the compile order decide.

Nothing here decides how any of it LOOKS — where a paragraph breaks,
what a slash token is drawn as, how a count is worded — because that is
the page's business and the page is the only caller. And nothing here
reaches past `backend`: the facts come from `backend.api.reports`, the
language from `backend.commands`, so the web says exactly what the
terminal says.

Bodies cross VERBATIM, exactly as they were typed or as they streamed.
The web is the second reader of the same store, not a second author of
its text.
"""

from collections.abc import Mapping
from typing import TypeVar

from otaku.web.api import cards, lore, play, providers, reports, settings, stories, transfer
from otaku.web.api.play import event, files_from, regenerate, syntax
from otaku.web.api.reports import context
from otaku.web.api.request import Ask, Blob, Created, Flow, NotFound, Pending, Route
from otaku.web.api.stories import facts, story, story_settings

# `play`, `stories` and `settings` are the modules here; their namesake
# functions are reached through them (`play.play`, `stories.stories`).
__all__ = [
    "FLOWS",
    "ROUTES",
    "Ask",
    "Blob",
    "Created",
    "Flow",
    "NotFound",
    "Pending",
    "Route",
    "cards",
    "context",
    "event",
    "facts",
    "files_from",
    "lore",
    "play",
    "providers",
    "regenerate",
    "reports",
    "settings",
    "stories",
    "story",
    "story_settings",
    "syntax",
    "transfer",
]


_Row = TypeVar("_Row")


def _merged(*tables: Mapping[tuple[str, str], _Row]) -> dict[tuple[str, str], _Row]:
    """The modules' rows as one table; a key two modules claim is a bug,
    caught here rather than by whichever import ran last."""
    out: dict[tuple[str, str], _Row] = {}
    for table in tables:
        twice = set(out) & set(table)
        if twice:
            raise AssertionError(f"a path in two tables: {sorted(twice)}")
        out.update(table)
    return out


# Every path the page may ask for whose work begins and ends inside the
# request, by METHOD and template. The method IS the lane (`web.server`):
# a GET only reads the session and is answered in the gaps of a streaming
# reply, and anything else takes the thread in turn. `{name}` in a
# template is a path parameter, and reaches the handler as
# `ask.params[name]`. The paths that span two requests are `FLOWS`, below.
#
# Four paths are NOT here, because none of them touch the session's
# thread: `/api/status` and `/api/watch` never do, `GET .../extraction`
# reads a run's own channel-safe poll, and the two that PLAY answer with
# a stream rather than a payload. The server holds those itself.
ROUTES: dict[tuple[str, str], Route] = _merged(
    play.ROUTES,
    stories.ROUTES,
    lore.ROUTES,
    providers.ROUTES,
    settings.ROUTES,
    reports.ROUTES,
    transfer.ROUTES,
)

# The five paths whose work outlives the request that started it — a
# document that lands and starts a pass, a card waiting on its persona
# answer, a pass the page polls. Matched exactly as `ROUTES` is, and
# answered on the same lane; the third argument is the whole difference.
FLOWS: dict[tuple[str, str], Flow] = _merged(
    stories.FLOWS,
    lore.FLOWS,
    cards.FLOWS,
)
