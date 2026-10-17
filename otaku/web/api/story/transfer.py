"""Stories crossing otaku's edge, as the page asks: a document landed as
a story — an otaku export, a SillyTavern chat, or plain text — and a
story exported as one document. Content-shaped both ways: a path over
HTTP would name a file on the SERVER, so the page sends what it read
and saves what it is given, where the reader says."""

from otaku.backend.api import Api
from otaku.web.api._request import Ask, Created, Route, changed, landed_story, plain
from otaku.web.api.story.stories import tagged


def _import(api: Api, ask: Ask) -> Created:
    """A story read out of an uploaded document, answered with where it
    now lives — and, for a document that carries no memory, the
    extraction pass started on it (`extraction`), polled where a forced
    pass is."""
    landed = api.imports.create(text=ask.need("text"), file_name=ask.need("file_name"))
    return Created(changed(landed), landed_story(landed.value.story_id))


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("POST", "/api/imports"): _import,
    ("GET", "/api/stories/{story}/export"): tagged(
        lambda api, ask: plain(api.stories.export.get(ask.id("story")))
    ),
}
