"""A story's memory, as the page asks for it: the whole of it in one
read, a scene, the cast — the composer's name menu among it — a
character and the merge that folds one into another, a journal record,
each corrected where a reader may; and the extraction pass the page
starts, polls and stops — kept by the backend, per story."""

from typing import Any

from otaku.backend.api import UNCHANGED, Api, Unchanged
from otaku.web.api._request import (
    Accepted,
    Ask,
    Route,
    changed,
    checked_integer,
    checked_strings,
    plain,
)
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
    """A character's description and profile — whole as TOML, or field by
    field (`profile_fields`) — whether its card's picture rides
    (`portrait_sent`), and its link to a codex card: `card_id`, a card's
    id or null to unlink, its copies in forked stories linked too where
    `with_copies` is true. A link alone is vetted with `?validate_only=true`,
    answered with how many copies."""
    if ask.query_flag("validate_only", False):
        return plain(
            api.stories.characters.update(
                ask.id("story"),
                ask.id("character"),
                card_id=ask.integer("card_id"),
                validate_only=True,
            )
        )
    card_id: int | Unchanged | None = UNCHANGED
    if "card_id" in ask.body:
        card_id = None if ask.body["card_id"] is None else ask.integer("card_id")
    return changed(
        api.stories.characters.update(
            ask.id("story"),
            ask.id("character"),
            description=ask.need("description") if "description" in ask.body else UNCHANGED,
            profile=ask.need("profile") if "profile" in ask.body else UNCHANGED,
            card_id=card_id,
            profile_fields=_profile_fields(ask.body["profile_fields"])
            if "profile_fields" in ask.body
            else UNCHANGED,
            portrait_sent=ask.flag("portrait_sent") if "portrait_sent" in ask.body else UNCHANGED,
            with_copies=ask.flag("with_copies") if "with_copies" in ask.body else False,
        )
    )


def _profile_fields(value: object) -> dict[str, object]:
    """A card's fields as the body carries them: an object of texts, the
    two lists of texts, the reminder's depth a whole number. A field
    the card has not passes, for the backend to refuse by name; anything
    else is a malformed request."""
    if not isinstance(value, dict):
        raise TypeError("profile_fields is not an object")
    for name, field in value.items():
        if name in ("alternate_greetings", "examples"):
            value[name] = checked_strings(field, name)
        elif name == "reminder_depth":
            checked_integer(field, name)
        elif not isinstance(field, str):
            raise TypeError(f"{name} is not a string")
    return value


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
