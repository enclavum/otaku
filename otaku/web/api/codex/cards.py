"""The codex's character cards, as the page asks for them: the list and
one card whole, a card made — by hand, from a card file the page uploads
(vetted first with `validate_only`, nothing stored, then sent again to
import: nothing waits on the server between the two), or from a story's
character — corrected, deleted; a picture added, a card merged into
another, and every story's character no card claims."""

import base64
from typing import Any

from otaku.backend.api import UNCHANGED, Api, Unchanged
from otaku.backend.api.codex.cards import RawFile
from otaku.web.api._request import Ask, Created, Route, changed, plain


def _create(api: Api, ask: Ask) -> Any:
    """A card made from what the body carries: `data` (a card file, as
    base64) and the `file_name` it was picked as — vetted alone with
    `?validate_only=true`, else imported, `reuse` naming the card the
    reader says it is; `story_id` and `character_id` — a story's
    character promoted, its copies in forked stories linked too where
    `with_copies` is true, vetted alone with `?validate_only=true` and
    answered with how many copies; or a `name` and the card's fields.
    Answered with where the card lives, made now (201) or held already
    (200)."""
    if "data" in ask.body:
        data = base64.b64decode(ask.need("data"), validate=True)
        file_name, rename = ask.need("file_name"), ask.text("rename")
        if ask.query_flag("validate_only", False):
            return plain(
                api.cards.create(data=data, file_name=file_name, rename=rename, validate_only=True)
            )
        reuse = ask.integer("reuse") if ask.body.get("reuse") is not None else None
        imported = api.cards.create(data=data, file_name=file_name, rename=rename, reuse=reuse)
        if not imported.value.made:
            return changed(imported)
        return Created(changed(imported), f"/api/cards/{imported.value.card.id}")
    if "story_id" in ask.body:
        story_id, character_id = ask.integer("story_id"), ask.integer("character_id")
        if ask.query_flag("validate_only", False):
            return plain(
                api.cards.create(story_id=story_id, character_id=character_id, validate_only=True)
            )
        promoted = api.cards.create(
            story_id=story_id,
            character_id=character_id,
            with_copies=ask.flag("with_copies") if "with_copies" in ask.body else False,
        )
        return Created(changed(promoted), f"/api/cards/{promoted.value.id}")
    made = api.cards.create(
        name=ask.need("name") if "name" in ask.body else None,
        aliases=ask.strings("aliases"),
        tagline=ask.text("tagline"),
        description=ask.text("description"),
        personality=ask.text("personality"),
        scenario=ask.text("scenario"),
        greeting=ask.text("greeting"),
        alternate_greetings=ask.strings("alternate_greetings"),
        examples=ask.strings("examples"),
        reminder=ask.text("reminder"),
        reminder_depth=ask.integer("reminder_depth") if "reminder_depth" in ask.body else 2,
        system_prompt=ask.text("system_prompt"),
        post_history_instructions=ask.text("post_history_instructions"),
        creator_notes=ask.text("creator_notes"),
    )
    return Created(changed(made), f"/api/cards/{made.value.id}")


def _update(api: Api, ask: Ask) -> Any:
    """The parts of a card the body carries, each replacing what stood;
    `pictures` each of the card's own by name, in their new order;
    `before` the card it now goes just before, null for last."""

    def text(key: str) -> str | Unchanged:
        return ask.need(key) if key in ask.body else UNCHANGED

    def strings(key: str) -> tuple[str, ...] | Unchanged:
        return ask.strings(key) if key in ask.body else UNCHANGED

    return changed(
        api.cards.update(
            ask.id("card"),
            name=text("name"),
            aliases=strings("aliases"),
            tagline=text("tagline"),
            description=text("description"),
            personality=text("personality"),
            scenario=text("scenario"),
            greeting=text("greeting"),
            alternate_greetings=strings("alternate_greetings"),
            examples=strings("examples"),
            reminder=text("reminder"),
            reminder_depth=ask.integer("reminder_depth")
            if "reminder_depth" in ask.body
            else UNCHANGED,
            system_prompt=text("system_prompt"),
            post_history_instructions=text("post_history_instructions"),
            creator_notes=text("creator_notes"),
            pictures=strings("pictures"),
            before=(None if ask.body["before"] is None else ask.integer("before"))
            if "before" in ask.body
            else UNCHANGED,
        )
    )


def _add_picture(api: Api, ask: Ask) -> Any:
    """One picture in the body — its bytes as base64 and the `name` it was
    picked as — added after the card's others."""
    file = RawFile(base64.b64decode(ask.need("data"), validate=True), ask.text("name"))
    return changed(api.cards.pictures.create(ask.id("card"), file=file))


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/cards"): lambda api, ask: {
        "items": plain(api.cards.list(query=ask.query.get("query", "")))
    },
    ("POST", "/api/cards"): _create,
    ("GET", "/api/cards/unlinked"): lambda api, ask: {
        "items": plain(api.cards.unlinked.list(query=ask.query.get("query", "")))
    },
    ("GET", "/api/cards/{card}"): lambda api, ask: plain(api.cards.get(ask.id("card"))),
    ("PATCH", "/api/cards/{card}"): _update,
    ("DELETE", "/api/cards/{card}"): lambda api, ask: changed(api.cards.delete(ask.id("card"))),
    ("POST", "/api/cards/{card}/pictures"): _add_picture,
    ("DELETE", "/api/cards/{card}/pictures/{name}"): lambda api, ask: changed(
        api.cards.pictures.delete(ask.id("card"), ask.params["name"])
    ),
    ("POST", "/api/cards/{card}/merges"): lambda api, ask: changed(
        api.cards.merges.create(ask.id("card"), into=ask.integer("into"))
    ),
}
