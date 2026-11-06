"""The codex's character cards, as the page asks for them: the list and
one card whole, a card made — by hand, from a card file or a picture the
page uploads (vetted first with `validate_only`, nothing stored, then sent
again to import: nothing waits on the server between the two), from a
link, fetched by the server, or from a story's character — corrected,
deleted; a picture added, a card written by
the model in use from its pictures, a card merged into another, and every
story's character no card claims."""

import base64
from typing import Any

from otaku.backend.api import UNCHANGED, Api, Unchanged
from otaku.backend.codex.cards.schemas import RawFile
from otaku.web.api._request import Ask, Created, Route, changed, plain, unqueued


def _create(api: Api, ask: Ask) -> Any:
    """A card made from what the body carries: `data` (a card file, or a
    picture that holds none, as base64) and the `file_name` it was picked
    as — vetted alone with `?validate_only=true`, under `name` where the
    body carries one, else imported under the `name` it must carry,
    `reuse` naming the card the reader says it is, `tagline` the made
    card's (a site's, the file came from a link to); `story_id` and `character_id` — a story's
    character promoted, its copies in forked stories linked too where
    `with_copies` is true, vetted alone with `?validate_only=true` and
    answered with how many copies; or a `name` and the card's fields.
    Answered with where the card lives, made now (201) or held already
    (200)."""
    if "data" in ask.body:
        data = base64.b64decode(ask.need("data"), validate=True)
        file_name = ask.need("file_name")
        if ask.query_flag("validate_only", False):
            name = ask.text("name") if "name" in ask.body else None
            return plain(
                api.cards.create(data=data, file_name=file_name, name=name, validate_only=True)
            )
        reuse = ask.integer("reuse") if ask.body.get("reuse") is not None else None
        imported = api.cards.create(
            data=data,
            file_name=file_name,
            name=ask.need("name"),
            reuse=reuse,
            tagline=ask.text("tagline"),
        )
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


@unqueued
def _fetch(api: Api, ask: Ask) -> Any:
    """The card a link gives — a card site's character page, or the file's
    own link — fetched and imported as a card file is: made now (201)
    or held already (200); with `?validate_only=true` fetched and
    vetted, nothing stored, and answered with the file it gave, for the
    page to import as a file. Off the app's thread: a site taking its
    time holds nothing else up."""
    if ask.query_flag("validate_only", False):
        return plain(api.cards.fetches.create(url=ask.need("url"), validate_only=True))
    imported = api.cards.fetches.create(url=ask.need("url"))
    if not imported.value.made:
        return changed(imported)
    return Created(changed(imported), f"/api/cards/{imported.value.card.id}")


@unqueued
def _generate(api: Api, ask: Ask) -> Any:
    """The card written by the model in use from its pictures, in the
    body's `language` where it names one. Off the app's thread: a model
    reading pictures for a minute holds nothing else up."""
    if "language" in ask.body:
        return changed(api.cards.generation.create(ask.id("card"), language=ask.need("language")))
    return changed(api.cards.generation.create(ask.id("card")))


@unqueued
def _cancel_generation(api: Api, ask: Ask) -> Any:
    """The card's generation given up. Off the app's thread, so a reply
    holding it never holds the cancel."""
    return changed(api.cards.generation.delete(ask.id("card")))


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/cards"): lambda api, ask: {
        "items": plain(api.cards.list(query=ask.query.get("query", "")))
    },
    ("POST", "/api/cards"): _create,
    ("POST", "/api/cards/fetches"): _fetch,
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
    ("POST", "/api/cards/{card}/generation"): _generate,
    ("DELETE", "/api/cards/{card}/generation"): _cancel_generation,
    ("POST", "/api/cards/{card}/merges"): lambda api, ask: changed(
        api.cards.merges.create(ask.id("card"), into=ask.integer("into"))
    ),
}
