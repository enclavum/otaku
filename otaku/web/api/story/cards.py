"""Character cards, as the page brings them into a story — a file's
bytes, which go into the codex first, or a codex card, invited: read and
vetted for the story — `validate_only`, nothing stored, what the persona
ask is drawn from — then brought in, the persona answered, in one
request of its own. The page sends the file both times: nothing waits on
the server between the two. A card from a link: read for the story by
the server, the file it gave answered with the reading, which the page
then sends as any file — the link fetched once."""

import base64
from typing import Any

from otaku.backend.api import Api
from otaku.web.api._request import Ask, Created, Route, changed, plain, unqueued
from otaku.web.api.story.stories import expected_head, tagged


def _create(api: Api, ask: Ask) -> Any:
    """The card in the body — `data`, the file's bytes as base64, and the
    `file_name` it was picked as (`reuse` the codex card the reader says
    it is, `tagline` a card made in the codex takes: a site's, the file
    came from a link to), or `card_id`, a codex card — read for the story
    under the `name` the body asks, which an import must carry and a check
    may (the card's own answered where it does not). Vetted alone with
    `?validate_only=true`;
    otherwise brought in, its portrait on its row where `with_pictures` asks,
    the story alone holding a file's card where `story_only` asks, played
    on from the head `If-Match` names, and answered with where the
    character it became now lives. Bytes-shaped because a path over HTTP
    would name a file on the SERVER."""
    story_id = ask.id("story")
    data = base64.b64decode(ask.need("data"), validate=True) if "data" in ask.body else None
    file_name = ask.need("file_name") if data is not None else ""
    card_id = ask.integer("card_id") if "card_id" in ask.body else None
    if ask.query_flag("validate_only", False):
        return plain(
            api.stories.cards.create(
                story_id,
                data=data,
                file_name=file_name,
                card_id=card_id,
                name=ask.text("name") if "name" in ask.body else None,
                validate_only=True,
            )
        )
    imported = api.stories.cards.create(
        story_id,
        data=data,
        file_name=file_name,
        card_id=card_id,
        name=ask.need("name"),
        reuse=ask.integer("reuse") if ask.body.get("reuse") is not None else None,
        tagline=ask.text("tagline"),
        persona=ask.need("persona"),
        with_pictures=ask.flag("with_pictures") if "with_pictures" in ask.body else False,
        story_only=ask.flag("story_only") if "story_only" in ask.body else False,
        expected_head=expected_head(api, ask, story_id),
    )
    return Created(
        changed(imported),
        f"/api/stories/{story_id}/characters/{imported.value.character_id}",
    )


def _fetch(api: Api, ask: Ask) -> Any:
    """The card a link gives — a card site's character page, or the file's
    own link — read for the story: with `?validate_only=true` fetched
    and vetted, nothing stored, and answered with the file it gave, for
    the page to bring in as a file; otherwise fetched and brought in
    as a file is, under the `name` the body carries. Off the app's
    thread: a site taking its time holds nothing else up."""
    story_id = ask.id("story")
    url = ask.need("url")
    if ask.query_flag("validate_only", False):
        name = ask.text("name") if "name" in ask.body else None
        return plain(
            api.stories.cards.fetches.create(story_id, url=url, name=name, validate_only=True)
        )
    imported = api.stories.cards.fetches.create(
        story_id,
        url=url,
        name=ask.need("name"),
        persona=ask.need("persona"),
        with_pictures=ask.flag("with_pictures") if "with_pictures" in ask.body else False,
        expected_head=expected_head(api, ask, story_id),
    )
    return Created(
        changed(imported),
        f"/api/stories/{story_id}/characters/{imported.value.character_id}",
    )


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("POST", "/api/stories/{story}/cards"): tagged(_create),
    ("POST", "/api/stories/{story}/cards/fetches"): unqueued(tagged(_fetch)),
}
