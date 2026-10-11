"""Character cards, as the page imports them: the file's bytes read and
vetted for the story — `validate_only`, nothing stored, what the persona
ask is drawn from — then imported, the persona answered, in one request
of its own. The page sends the file both times: nothing waits on the
server between the two."""

import base64
from typing import Any

from otaku.backend.api import Api
from otaku.web.api._request import Ask, Created, Route, changed, plain
from otaku.web.api.story.stories import expected_head, tagged


def _create(api: Api, ask: Ask) -> Any:
    """The card in the body — `data`, the file's bytes as base64, and the
    `file_name` it was picked as — read for the story, renamed where the
    body asks. Vetted alone with `?validate_only=true`; otherwise
    imported, played on from the head `If-Match` names, and answered
    with where the character it became now lives. Bytes-shaped because a
    path over HTTP would name a file on the SERVER."""
    story_id = ask.id("story")
    data = base64.b64decode(ask.need("data"), validate=True)
    file_name, rename = ask.need("file_name"), ask.text("rename")
    if ask.query_flag("validate_only", False):
        return plain(
            api.stories.cards.create(
                story_id, data=data, file_name=file_name, rename=rename, validate_only=True
            )
        )
    imported = api.stories.cards.create(
        story_id,
        data=data,
        file_name=file_name,
        rename=rename,
        persona=ask.need("persona"),
        expected_head=expected_head(api, ask, story_id),
    )
    return Created(
        changed(imported),
        f"/api/stories/{story_id}/characters/{imported.value.character_id}",
    )


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("POST", "/api/stories/{story}/cards"): tagged(_create),
}
