"""Stories, as the page asks for them: the session's facts, the listing
and one story whole, its head, premise, title, settings and export, a
turn corrected, a fork — and the one flow here, a story made from a
title or an uploaded document."""

from typing import Any

from otaku import __version__
from otaku.backend import InjectionPosition
from otaku.backend.api import stories as api_stories
from otaku.backend.session import Session
from otaku.backend.story import InjectingSetting
from otaku.formatting import format_context
from otaku.web.api import transfer
from otaku.web.api.lore import memory
from otaku.web.api.play import turn
from otaku.web.api.request import Ask, Created, Flow, NotFound, Pending, Route, landed_story


def facts(session: Session) -> dict[str, Any]:
    """What the rail and the runhead draw, without reading a story or
    probing a provider. Best-effort like the terminal's own opening: a
    cloud catalog is never asked for its context window here.

    The knobs are NOT here — they are `settings`, and a figure with two
    homes has two truths — and neither is the premise, which belongs to
    the story that is sent with it."""
    max_context = session.max_context()
    return {
        "version": __version__,
        "model": session.model or "(no model)",
        "provider": session.provider,
        "max_context": format_context(max_context) if max_context else "",
        "story": api_stories.headline(session),
        "story_id": session.story_id,
        # How many, so the runhead needs no chain.
        "turns": len(session.messages),
        # Whether the model in use takes pictures — what shows the attach
        # button; the backend refuses a picture regardless.
        "vision": session.vision,
    }


def stories(session: Session, query: str = "") -> list[dict[str, Any]]:
    """The story browser's rows, most recently played first. `label` is
    the listing's own fallback rule — title, then the newest rollup,
    then the first prompt — resolved in its one home, never here.

    `query` filters the collection on the ONE rule both browsers
    promise: a story matches on its current chain's text OR on its
    listing row's face. The union is the backend's — the browsers agree
    because neither composes it."""
    open_id = session.story_id
    matched = set(api_stories.search(session, query)) if query else None
    return [
        {
            "id": row.id,
            "label": row.label,
            "title": row.title,
            "story_so_far": row.story_so_far,
            "first_user": row.first_user,
            "model": row.model,
            "updated_at": row.updated_at.isoformat(),
            # A count is `turns`; the chain itself is `messages`, and it
            # comes with the story rather than with the listing.
            "turns": row.num_messages,
            "open": row.id == open_id,
        }
        for row in api_stories.listing(session)
        if matched is None or row.id in matched
    ]


def story(session: Session, story_id: int) -> dict[str, Any]:
    """One story, WHOLE: everything the dossier's four tabs draw, in one
    read. Any story's, not only the open one.

    One read and not three, because the panel opens all four tabs from
    one place: asking three times for one subject lets an extraction
    pass land between two of the asks and hand the page a torn story —
    scenes covering messages it was told nothing about."""
    listing = next((row for row in api_stories.listing(session) if row.id == story_id), None)
    if listing is None:
        # The store answers empties for an id it does not hold, and an
        # empty dossier dressed as a story would be a lie — the spec's
        # 404 is the truth (a browser row deleted from another tab).
        raise NotFound(f"no story {story_id}")
    return {
        "id": story_id,
        "label": listing.label,
        "title": listing.title,
        "updated_at": listing.updated_at.isoformat(),
        "premise": api_stories.get_system(session, story_id),
        "messages": [turn(message) for message in api_stories.messages_of(session, story_id)],
        # the lore half — scenes, characters, how far the pass has read
        **memory(session, story_id),
    }


def _head(session: Session, ask: Ask) -> str:
    """Where the session is reading. `discard` sets the later turns
    aside; without it they stay in the database above the tail. A resume
    may name no message — a story with nothing played has none, and a
    resume never used one — where discarding without one is malformed."""
    action: Any = "truncate" if ask.body.get("discard") else "resume"
    message = ask.body.get("message")
    return api_stories.land(
        session, int(ask.body["story"]), None if message is None else int(message), action
    )


def story_settings(session: Session, story_id: int) -> dict[str, Any]:
    """A story's settings as they stand, in the order they are sent: the
    switch, where it injects (null for one that injects nothing) and the
    positions it may take, and the story's own text (null for one with
    none). Any story's; a story that is not there is the spec's 404."""
    if all(row.id != story_id for row in api_stories.listing(session)):
        raise NotFound(f"no story {story_id}")
    return {
        "settings": [
            {
                "name": setting.name,
                "label": setting.label,
                "injection_label": (
                    setting.injection_label if isinstance(setting, InjectingSetting) else None
                ),
                "tool": setting.tool.name if setting.tool else None,
                "allowed_positions": [each.value for each in setting.allowed_positions],
                "enabled": setting.enabled,
                "position": (
                    setting.injection_position.value
                    if setting.injection_position is not None
                    else None
                ),
                "reminder_text": setting.reminder_text,
                "display_notes": setting.display_notes,
            }
            for setting in api_stories.get_settings(session, story_id)
        ]
    }


def _update_setting(session: Session, ask: Ask) -> str:
    """One setting of a story, the fields given laid over it; what each
    may take is the setting's to refuse (`backend.story`). A flag that
    is not a boolean is a malformed request, as a null title is."""
    story_id = ask.id("story")
    if all(row.id != story_id for row in api_stories.listing(session)):
        raise NotFound(f"no story {story_id}")
    flags = {}
    for name in ("enabled", "display_notes"):
        flags[name] = ask.body.get(name)
        if flags[name] is not None and not isinstance(flags[name], bool):
            raise TypeError(f"{name} is not a boolean")
    reminder_text = ask.text("reminder_text") if "reminder_text" in ask.body else None
    # "system" or a count from 1 is a position (which the setting may
    # still refuse); anything else is not one, a malformed request.
    position = None
    if (raw := ask.body.get("position")) is not None:
        position = InjectionPosition.from_value(raw)
        if position is None:
            raise TypeError('position is not "system" or a count from 1')
    return api_stories.update_setting(
        session,
        ask.params["setting"],
        story_id=story_id,
        enabled=flags["enabled"],
        position=position,
        reminder_text=reminder_text,
        display_notes=flags["display_notes"],
    )


def _premise(session: Session, ask: Ask) -> str:
    """A story's premise. A body rather than a line: a premise is
    paragraphs. Any story's — the dossier edits one from the outside as
    readily as the open one."""
    return api_stories.set_system(session, ask.need("text"), ask.id("story"))


def _title(session: Session, ask: Ask) -> str:
    return api_stories.set_title(session, ask.need("title"), ask.id("story"))


def _delete_story(session: Session, ask: Ask) -> str:
    story_id = ask.id("story")
    if all(row.id != story_id for row in api_stories.listing(session)):
        # A DELETE that removed nothing must not say it did: the row is
        # already gone (another tab's delete), and the spec's 404 says so.
        raise NotFound(f"no story {story_id}")
    api_stories.delete(session, story_id)
    return "Story deleted."


def _fork(session: Session, ask: Ask) -> Created:
    """A copy of the story, from its head or from one message on. It
    MAKES a story, so it answers with where the copy now lives."""
    message = ask.body.get("message")
    title = ask.text("title")
    if message is None:
        # The whole story the PATH names, which is not always the open
        # one: a copy made from a browser row must not silently copy
        # whatever happens to be open instead.
        said = api_stories.fork(session, title, ask.id("story"))
    else:
        said = api_stories.land(session, ask.id("story"), int(message), "fork")
    return Created(said, landed_story(session), {"story": session.story_id})


def _edit_message(session: Session, ask: Ask) -> str:
    api_stories.edit_message(session, ask.id("message"), ask.need("text"), story_id=ask.id("story"))
    return "Message edited."


def _new_story(session: Session, ask: Ask, pending: Pending) -> Created:
    """A story, made. Empty with a title (or nothing) — or read out of an
    uploaded document, which is `transfer.imported`'s: an otaku export, a
    SillyTavern chat, or plain text."""
    document: Any = ask.body.get("import")
    if not document:
        said = api_stories.new(session, ask.text("title"))
        # The id it made, so a page that needs a story to address — a
        # premise written before the first message — can address this one
        # without reading `Location` back apart.
        return Created(said, landed_story(session), {"story": session.story_id})
    return transfer.imported(session, pending, str(document["text"]), str(document["name"]))


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/session"): lambda session, ask: facts(session),
    ("PUT", "/api/session/head"): _head,
    # All stories
    ("GET", "/api/stories"): lambda session, ask: {
        "stories": stories(session, ask.query.get("q", ""))
    },
    ("GET", "/api/stories/{story}"): lambda session, ask: story(session, ask.id("story")),
    ("DELETE", "/api/stories/{story}"): _delete_story,
    ("PUT", "/api/stories/{story}/title"): _title,
    ("POST", "/api/stories/{story}/fork"): _fork,
    ("PATCH", "/api/stories/{story}/messages/{message}"): _edit_message,
    # Inside a story
    ("PUT", "/api/stories/{story}/premise"): _premise,
    ("GET", "/api/stories/{story}/settings"): lambda session, ask: story_settings(
        session, ask.id("story")
    ),
    ("PATCH", "/api/stories/{story}/settings/{setting}"): _update_setting,
    # The reminder stories share: a text, the database's, not a knob of
    # the /set family.
    ("GET", "/api/shared_reminder"): lambda session, ask: {
        "text": api_stories.get_shared_reminder(session)
    },
    ("PUT", "/api/shared_reminder"): lambda session, ask: api_stories.set_shared_reminder(
        session, ask.need("text")
    ),
}


FLOWS: dict[tuple[str, str], Flow] = {
    ("POST", "/api/stories"): _new_story,
}
