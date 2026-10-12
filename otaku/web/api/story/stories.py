"""Stories, as the page asks for them: the collection, one story, and
the actions on one — a copy, a fresh take on the last reply (a write
whose answer is the reply's event stream, `STREAMS`), the trailing
exchange taken back — and the two rules every path inside a story
keeps, kept here, with the story: an answer is told with the head the
story stands at (`tagged`), and a write that moves the head names the
one it moves from (`expected_head`)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from otaku.backend.api import Api, HeadMoved
from otaku.web.api._request import (
    Ask,
    Created,
    PreconditionRequired,
    Route,
    Stream,
    Tagged,
    changed,
    etag,
    landed_story,
    plain,
)

if TYPE_CHECKING:  # circular at runtime: the messages twin takes `tagged` from here
    from otaku.web.api.story.messages import PlayEvent

# One strong entity tag naming a head, as `etag` writes it.
_HEAD_TAG = re.compile(r'"(\d+)"')


def tagged(route: Route) -> Route:
    """A route inside one story, its answer told with the head the story
    stands at once it is given — read in the same job, on the app's one
    thread, so the tag is the head the answer was made at. An answer
    already tagged is left as it is."""

    def answer(api: Api, ask: Ask) -> Tagged:
        given = route(api, ask)
        if isinstance(given, Tagged):
            return given
        return Tagged(given, api.stories.get(ask.id("story")).head)

    return answer


def expected_head(api: Api, ask: Ask, story_id: int) -> int | None:
    """The head a write that moves `story_id`'s head plays on from, as
    the request's `If-Match` names it: the one tag the client read, or
    `*` — the head as it stands. Missing is a precondition the client
    owed (`PreconditionRequired`); tags that name none of the heads this
    story could stand at are a precondition that failed (`HeadMoved`,
    saying where the head stands now). One tag goes to the backend as it
    is, whose compare-and-set decides."""
    tags = ask.if_match
    if tags is None:
        raise PreconditionRequired(
            "this write moves the story's head: If-Match must name the ETag it was read at"
        )
    stated = [tag.strip() for tag in tags.split(",")]
    if stated == ["*"]:
        return api.stories.get(story_id).head
    found = _HEAD_TAG.fullmatch(stated[0]) if len(stated) == 1 else None
    if found is not None:
        return int(found.group(1)) or None
    current = api.stories.get(story_id).head
    if etag(current) in stated:
        return current
    raise HeadMoved(current)


def _get(api: Api, ask: Ask) -> Tagged:
    found = api.stories.get(ask.id("story"))
    return Tagged(plain(found), found.head)


def _create(api: Api, ask: Ask) -> Created:
    """A story, made at once: its title, its premise, or neither — and
    the codex cards it starts with (`card_ids`, in pick order),
    `card_pictures` the cards whose portraits ride, `persona` what their
    `{{user}}` binds to."""
    made = api.stories.create(
        title=ask.text("title"),
        premise=ask.text("premise"),
        card_ids=_ids(ask, "card_ids"),
        card_pictures=_ids(ask, "card_pictures"),
        persona=ask.text("persona"),
    )
    return Created(changed(made), landed_story(made.value.id))


def _ids(ask: Ask, name: str) -> tuple[int, ...]:
    """A body field that is a JSON list of row ids, () where the body
    leaves it out; anything else is a malformed request."""
    value = ask.body.get(name, [])
    if not isinstance(value, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        raise TypeError(f"{name} is not a list of ids")
    return tuple(value)


def _update(api: Api, ask: Ask) -> Tagged:
    """A story's title, its premise — paragraphs, taken verbatim — and its
    head: rewound to one of its messages, the messages after it kept in the
    tree. Moving the head names the one it moves from (`If-Match`); the
    two texts are written whatever it stands at."""
    story_id = ask.id("story")
    fields: dict[str, Any] = {}
    for name in ("title", "premise"):
        if name in ask.body:
            fields[name] = ask.need(name)
    if "head" in ask.body:
        fields["head"] = ask.integer("head")
        fields["expected_head"] = expected_head(api, ask, story_id)
    made = api.stories.update(story_id, **fields)
    return Tagged(changed(made), made.value.head)


def _fork(api: Api, ask: Ask) -> Created:
    """A copy of the story — from its head, or from one of its messages
    (`at`) — under `title` ("" numbers one). It MAKES a story, so it
    answers with where the copy now lives."""
    at = ask.integer("at") if ask.body.get("at") is not None else None
    forked = api.stories.forks.create(ask.id("story"), at=at, title=ask.text("title"))
    return Created(changed(forked), landed_story(forked.value.id))


def _regenerate(api: Api, ask: Ask) -> Iterator[PlayEvent]:
    """The story's standing reply set aside as a sibling and a fresh take
    streamed, from the head `If-Match` names. Eager like a line played
    (`messages`): without a model, or with nothing to regenerate, it
    refuses before anything is dropped."""
    story_id = ask.id("story")
    return api.stories.regens.create(story_id, expected_head=expected_head(api, ask, story_id))


def _undo(api: Api, ask: Ask) -> dict[str, Any]:
    """The trailing exchange taken back, from the head `If-Match` names —
    the story as it now stands."""
    story_id = ask.id("story")
    return changed(
        api.stories.undos.create(story_id, expected_head=expected_head(api, ask, story_id))
    )


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/stories"): lambda api, ask: {
        "items": plain(api.stories.list(query=ask.query.get("query", "")))
    },
    ("POST", "/api/stories"): _create,
    ("GET", "/api/stories/{story}"): _get,
    ("PATCH", "/api/stories/{story}"): _update,
    ("DELETE", "/api/stories/{story}"): lambda api, ask: changed(
        api.stories.delete(ask.id("story"))
    ),
    ("POST", "/api/stories/{story}/forks"): _fork,
    ("POST", "/api/stories/{story}/undos"): tagged(_undo),
}

# The write whose answer is the reply as it arrives — matched as the
# routes are, answered by the server's stream (`web.server.streams`).
STREAMS: dict[tuple[str, str], Stream] = {
    ("POST", "/api/stories/{story}/regens"): _regenerate,
}
