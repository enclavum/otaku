"""What the page asks for, as plain data — this frontend's whole surface
over the app, as tables of method and path.

`ROUTES` is the whole of it but two: one entry per method and path, each
taking the interface (`backend.api.Api`) and an `Ask` — what the path
named, what the query asked for, what the body carried, the head an
`If-Match` names — and returning what the page gets: a representation
as JSON writes it (`plain`), a write's answer (`changed`: the thing as
it now stands and the sentence), or one of the answers that say more —
`Created`, `Accepted`, `Tagged`, `Blob`. `STREAMS` are the two writes
whose answer is the reply as it arrives, each event named on the wire by
`event`. What is NOT here is HTTP: `web.server` matches a request against
these tables and carries the result, and nothing else.

The package mirrors `backend.api`, three packages of twins: `app`, the
installation (the app itself, `machine`, `settings`, `providers`,
`models`, `integrations`, `history`, `pictures`, `reports`), `codex`,
what every story may draw on (`cards`), and `story`, a story and what it
holds (`stories`, `settings`, `messages`, `lore`, `cards`, `context`,
`transfer`, `prompts`, `language`) — each module translating its twin's
answers into the page's and holding its own rows — and `_request`, the
layer's machinery: what every row is handed and hands back. A module
calls its own twin's resources and nothing else of the interface; what
it needs of another twin it takes from that twin's sibling here (every
path inside a story is `story.stories.tagged`, and a write that moves a
head asks `story.stories.expected_head`), which is what keeps every
translation of one twin in one module. The tables here are the modules'
rows merged, and a path in two of them is a fault at import: the server
matches one sorted list built from all of them, and a duplicate would
let the compile order decide.

The package is the web's ONE door to the backend: nothing else in the
web imports `otaku.backend.api` — the server takes the app, the facade,
the refusals and its settings slice from here, its root re-exporting the
interface's vocabulary and each twin the types the server needs of it.

Nothing here decides how any of it LOOKS — where a paragraph breaks,
what a slash token is drawn as, how a count is written — because that is
the page's business and the page is the only caller. And nothing here
reaches past `backend.api`, so the web says exactly what the terminal
says.

Bodies cross VERBATIM, exactly as they were typed or as they streamed.
The web is the second reader of the same store, not a second author of
its text.
"""

from collections.abc import Mapping
from typing import TypeVar

from otaku.backend.api import (
    NO_MODEL_HINT,
    NO_STORY_HINT,
    NO_SUCH_STORY,
    UNCHANGED,
    Actor,
    Api,
    App,
    Attachment,
    Changed,
    CompletionType,
    Conflict,
    HeadMoved,
    InjectionPosition,
    Invalid,
    JoinType,
    KeySource,
    Layout,
    Locality,
    ModelRef,
    ModelState,
    NotFound,
    Refused,
    StoryMode,
    Unavailable,
    Unchanged,
)
from otaku.web.api import app
from otaku.web.api._request import (
    Accepted,
    Ask,
    Blob,
    Created,
    PreconditionRequired,
    Route,
    Stream,
    Tagged,
    etag,
)
from otaku.web.api.app import (
    history,
    integrations,
    machine,
    models,
    pictures,
    providers,
    reports,
)
from otaku.web.api.app import settings as app_settings
from otaku.web.api.codex import cards as codex_cards
from otaku.web.api.story import (
    cards,
    context,
    language,
    lore,
    messages,
    prompts,
    stories,
    transfer,
)
from otaku.web.api.story import settings as story_settings
from otaku.web.api.story.messages import event

__all__ = [
    "NO_MODEL_HINT",
    "NO_STORY_HINT",
    "NO_SUCH_STORY",
    "ROUTES",
    "STREAMS",
    "UNCHANGED",
    "Accepted",
    "Actor",
    "Api",
    "App",
    "Ask",
    "Attachment",
    "Blob",
    "Changed",
    "CompletionType",
    "Conflict",
    "Created",
    "HeadMoved",
    "InjectionPosition",
    "Invalid",
    "JoinType",
    "KeySource",
    "Layout",
    "Locality",
    "ModelRef",
    "ModelState",
    "NotFound",
    "PreconditionRequired",
    "Refused",
    "Route",
    "StoryMode",
    "Stream",
    "Tagged",
    "Unavailable",
    "Unchanged",
    "app",
    "app_settings",
    "cards",
    "codex_cards",
    "context",
    "etag",
    "event",
    "history",
    "integrations",
    "language",
    "lore",
    "machine",
    "messages",
    "models",
    "pictures",
    "prompts",
    "providers",
    "reports",
    "stories",
    "story_settings",
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


# Every path the page may ask for whose answer is a payload, by METHOD
# and template. The method IS the lane (`web.server`): a GET only reads
# the app and is answered in the gaps of a streaming reply, and anything
# else takes the thread in turn. `{name}` in a template is a path
# parameter, and reaches the handler as `ask.params[name]`.
#
# Not here: the two writes that answer with a stream (`STREAMS`), and the
# paths the server holds itself — `/api/app/status` and `/api/watch`,
# which never touch the app's thread, and `/api/login`.
ROUTES: dict[tuple[str, str], Route] = _merged(
    app.ROUTES,
    machine.ROUTES,
    app_settings.ROUTES,
    providers.ROUTES,
    models.ROUTES,
    integrations.ROUTES,
    history.ROUTES,
    pictures.ROUTES,
    reports.ROUTES,
    codex_cards.ROUTES,
    stories.ROUTES,
    story_settings.ROUTES,
    messages.ROUTES,
    lore.ROUTES,
    cards.ROUTES,
    context.ROUTES,
    transfer.ROUTES,
    prompts.ROUTES,
    language.ROUTES,
)

# The two writes whose answer is the reply as it arrives: matched as the
# routes are, on the same lane, and answered as a stream of `event`s.
STREAMS: dict[tuple[str, str], Stream] = _merged(messages.STREAMS, stories.STREAMS)
