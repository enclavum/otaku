"""A story's memory, as the page asks for it: the whole of it in one
read, a scene, the cast — the composer's name menu among it — a
character and the merge that folds one into another, a journal record,
each corrected where a reader may; and the extraction pass the page
starts, polls and stops — kept by the backend, per story."""

from typing import Any

from otaku.backend.api import UNCHANGED, Api
from otaku.web.api._request import Accepted, Ask, Route, changed, plain
from otaku.web.api.story.stories import tagged


def _characters(api: Api, ask: Ask) -> dict[str, Any]:
    """The story's cast in appearance order — or the characters a `name`
    finds (a name or an alias, cased as the reader likes)."""
    name = ask.query.get("name")
    return {"items": plain(api.stories.characters.list(ask.id("story"), name=name))}


def _update_scene(api: Api, ask: Ask) -> Any:
    """A scene's own two fields. Its arc (`history`) is derived and has no
    write — correcting the entries rebuilds it."""
    return changed(
        api.stories.scenes.update(
            ask.id("story"),
            ask.id("scene"),
            title=ask.need("title") if "title" in ask.body else UNCHANGED,
            summary=ask.need("summary") if "summary" in ask.body else UNCHANGED,
        )
    )


def _update_character(api: Api, ask: Ask) -> Any:
    return changed(
        api.stories.characters.update(
            ask.id("story"),
            ask.id("character"),
            description=ask.need("description") if "description" in ask.body else UNCHANGED,
            card=ask.need("card") if "card" in ask.body else UNCHANGED,
        )
    )


def _update_journal(api: Api, ask: Ask) -> Any:
    """A journal record's entry. The state is the extractor's own — like
    both histories it has no write; correcting the entry is what moves
    the record."""
    return changed(
        api.stories.journals.update(
            ask.id("story"),
            ask.id("journal"),
            entry=ask.need("entry") if "entry" in ask.body else UNCHANGED,
        )
    )


def _merge(api: Api, ask: Ask) -> Any:
    """The character folded into the one `into` names — the merge
    answering the character it now is."""
    return changed(
        api.stories.characters.merges.create(
            ask.id("story"), ask.id("character"), into=ask.integer("into")
        )
    )


def _extract(api: Api, ask: Ask) -> Accepted:
    """An extraction pass over the story, now: started — or the one
    already running — and polled where it lives until it is done."""
    story_id = ask.id("story")
    started = api.stories.extraction.create(story_id)
    return Accepted(changed(started), f"/api/stories/{story_id}/extraction")


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/stories/{story}/lore"): tagged(
        lambda api, ask: plain(api.stories.lore.get(ask.id("story")))
    ),
    ("GET", "/api/stories/{story}/scenes"): tagged(
        lambda api, ask: {"items": plain(api.stories.scenes.list(ask.id("story")))}
    ),
    ("GET", "/api/stories/{story}/scenes/{scene}"): tagged(
        lambda api, ask: plain(api.stories.scenes.get(ask.id("story"), ask.id("scene")))
    ),
    ("PATCH", "/api/stories/{story}/scenes/{scene}"): tagged(_update_scene),
    ("GET", "/api/stories/{story}/characters"): tagged(_characters),
    ("GET", "/api/stories/{story}/characters/{character}"): tagged(
        lambda api, ask: plain(api.stories.characters.get(ask.id("story"), ask.id("character")))
    ),
    ("PATCH", "/api/stories/{story}/characters/{character}"): tagged(_update_character),
    ("DELETE", "/api/stories/{story}/characters/{character}"): tagged(
        lambda api, ask: changed(
            api.stories.characters.delete(ask.id("story"), ask.id("character"))
        )
    ),
    ("POST", "/api/stories/{story}/characters/{character}/merges"): tagged(_merge),
    ("GET", "/api/stories/{story}/journals"): tagged(
        lambda api, ask: {"items": plain(api.stories.journals.list(ask.id("story")))}
    ),
    ("GET", "/api/stories/{story}/journals/{journal}"): tagged(
        lambda api, ask: plain(api.stories.journals.get(ask.id("story"), ask.id("journal")))
    ),
    ("PATCH", "/api/stories/{story}/journals/{journal}"): tagged(_update_journal),
    ("POST", "/api/stories/{story}/extraction"): tagged(_extract),
    ("GET", "/api/stories/{story}/extraction"): tagged(
        lambda api, ask: plain(api.stories.extraction.get(ask.id("story")))
    ),
    ("DELETE", "/api/stories/{story}/extraction"): tagged(
        lambda api, ask: changed(api.stories.extraction.delete(ask.id("story")))
    ),
}
