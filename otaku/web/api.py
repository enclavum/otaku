"""What the page asks for, as plain data — this frontend's whole surface
over an open session, one table per kind of request.

`ROUTES` is the whole of it: one entry per method and path, each
taking the session and an `Ask` — what the path named, what the query
asked for, what the body carried — and returning something `json` can
write. A bare sentence back is a notice; a `Created` says a thing was
made and where it now lives. `Pending` holds what outlives a request,
and reaches only the rows of `FLOWS`. `play` and `regenerate`
return the reply's event stream and `event` names each event on the
wire. What is NOT here is HTTP: `web.server` matches a request against
this table and carries the result, and nothing else.

Nothing here decides how any of it LOOKS — where a paragraph breaks,
what a slash token is drawn as, how a count is worded — because that is
the page's business and the page is the only caller. And nothing here
reaches past `backend`: the facts come from `backend.api.reports`, the
language from `backend.commands`, so the web says exactly what the
terminal says.

Bodies cross VERBATIM, exactly as they were typed or as they streamed.
The web is the second reader of the same store, not a second author of
its text.
"""

import base64
import secrets
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from otaku import __version__
from otaku.backend import (
    Journal,
    Locality,
    Message,
    ModelInfo,
    ModelState,
    commands,
    meminfo,
)
from otaku.backend.api import cards as api_cards
from otaku.backend.api import lore as api_lore
from otaku.backend.api import play as api_play
from otaku.backend.api import providers as api_providers
from otaku.backend.api import reports
from otaku.backend.api import settings as api_settings
from otaku.backend.api import stories as api_stories
from otaku.backend.api import transfer as api_transfer
from otaku.backend.api.cards import PreparedCard
from otaku.backend.api.lore import FieldKind, WorkerRun
from otaku.backend.api.play import Declined, Done, Failed, PlayEvent, Reasoning, Recorded, Text
from otaku.backend.api.providers import SupportedProvider
from otaku.backend.commands import COMMANDS, PROSE_DESCRIPTION
from otaku.backend.session import EFFORTS, PARAMETERS, THINK_UNSET, Refused, Session
from otaku.formatting import Money, format_context, format_size

__all__ = [
    "FLOWS",
    "ROUTES",
    "Ask",
    "Created",
    "NotFound",
    "Pending",
    "context",
    "event",
    "facts",
    "play",
    "regenerate",
    "settings",
    "stories",
    "story",
    "syntax",
]

# ---------- what a request carries ----------


@dataclass(frozen=True)
class Ask:
    """One request as a handler sees it, with no HTTP in sight: what the
    PATH named, what the query asked for, and what the body carried.
    `web.server` takes a request apart and hands over these three."""

    params: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    body: Mapping[str, Any] = field(default_factory=dict)

    def id(self, name: str) -> int:
        """One numeric path segment. The router has already matched the
        template, so a value that is not a number is a bug here, not a
        request to refuse."""
        return int(self.params[name])

    def text(self, name: str, default: str = "") -> str:
        """A body field the request may leave out."""
        return str(self.body.get(name, default))

    def need(self, name: str) -> str:
        """A body field the request MUST carry. Missing is a malformed
        request — the server answers 400 — and never a silent default:
        a PATCH with no text would blank what it was meant to correct.
        A field sent as `null` is the same fault wearing a value, and is
        refused for the same reason: coerced, it would store the literal
        title "None"."""
        value = self.body[name]
        if value is None:
            raise TypeError(f"{name} is null")
        return str(value)


@dataclass(frozen=True)
class Created:
    """A write that MADE something: the sentence, and where the thing now
    lives. The server answers `201` and names it in `Location`; anything
    else a write returns is a plain `200`."""

    notice: str
    location: str
    extra: Mapping[str, Any] = field(default_factory=dict)


class NotFound(Exception):  # noqa: N818 — a 404 is an expected answer, not an error
    """The path parsed but the SUBJECT it names is not there — a story id
    the database does not hold, a provider name nothing is configured
    under. The server answers `404`, exactly as it does for a path that
    never matched: the spec draws no line between the two, and a body
    would say nothing the page could show."""


# ---------- what the page reads ----------


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
    }


def _turns(session: Session) -> list[dict[str, Any]]:
    """The open story, one row per stored turn, oldest first."""
    return [_turn(message) for message in session.messages]


def _history(session: Session) -> list[str]:
    """The composer's ↑/↓ history, most recent first — the same
    store-backed lines the terminal prompt walks, so a reload (or a
    session on the other frontend) starts with the history it left."""
    return session.history()


def syntax() -> dict[str, Any]:
    """The story's typed LANGUAGE — not its commands. The openers a line
    may start with and the inliners it may carry, which is what the
    composer's menu offers and the help sheet lists.

    Tokens and argument shapes only. What each one MEANS in a menu is
    the page's own caption: a sheet has room for a caption where the
    shared table's row is a sentence, and where a word is drawn is the
    medium's business. The rows themselves are declared once, in
    `context.syntax`, and reach here through the shared table."""
    rows = [spec for spec in COMMANDS if spec.kind is commands.CommandKind.SYNTAX]
    return {
        # What a line with no framing does — the one row that is not a
        # word, and the sentence the sheet opens with.
        "prose": PROSE_DESCRIPTION,
        "openers": [
            {"token": spec.token, "args": spec.args}
            for spec in rows
            if not spec.token.startswith("…")
        ],
        "inliners": [
            {"token": spec.token, "args": spec.args} for spec in rows if spec.token.startswith("…")
        ],
    }


def cast(session: Session) -> dict[str, Any]:
    """The open story's cast, for the composer's name menu — the cheap
    per-keystroke read `backend.api.lore.cast` exists for. Empty with no
    story or an empty cast: a menu question is never a refusal."""
    return {
        "characters": [
            {"id": row.id, "name": row.name, "description": row.description}
            for row in api_lore.cast(session)
        ]
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
    view = api_lore.view(session, story_id)
    return {
        "id": story_id,
        "label": listing.label,
        "title": listing.title,
        "updated_at": listing.updated_at.isoformat(),
        "premise": api_stories.get_system(session, story_id),
        "messages": [_turn(message) for message in api_stories.messages_of(session, story_id)],
        # How far the extractor has read, and what is still open — the
        # facts the index's pending row and the extract block draw.
        "read_through": view.read_through(),
        "unread": view.unread(),
        "unread_span": view.unread_span(),
        "scenes": [
            {
                "id": scene.id,
                "number": number,
                # From the view's own data: a scene whose span cannot be
                # computed drops it, which would make a title read as a
                # message range.
                "span": view.scene_span(scene.id),
                "title": scene.title,
                "summary": scene.summary,
                # The arc THROUGH this scene — derived, so no write takes
                # it — and when the extractor last wrote the scene (an
                # audit stamp, display alone).
                "history": scene.history,
                "updated_at": scene.updated_at,
                "present": [
                    character.name
                    for character in view.cast
                    if any(
                        journal.scene_id == scene.id and journal.character_id == character.id
                        for journal in view.journals
                    )
                ],
                "journals": [
                    _journal(journal) for journal in view.journals if journal.scene_id == scene.id
                ],
            }
            for number, scene in enumerate(view.scenes, start=1)
        ],
        "characters": [
            {
                "id": character.id,
                "name": character.name,
                "aliases": list(character.aliases),
                "description": character.description,
                # The archive an IMPORTED character carries; "" for one
                # the extractor named out of the story itself.
                "card": character.card or "",
                # Their arc SO FAR — derived, so no write takes it, the
                # way a scene's `history` above is derived.
                "history": view.character_history(character.id),
                # When the extractor last wrote them — display alone.
                "updated_at": character.updated_at,
                "journals": [
                    _journal(journal)
                    for journal in view.journals
                    if journal.character_id == character.id
                ],
            }
            for character in view.cast
        ],
    }


def _memory(session: Session) -> dict[str, Any]:
    """The machine's memory alone — the same gauge the picker opens with
    (`backend.meminfo`), on its own so the page can watch it fill while a
    model loads. Reading it costs one syscall; asking `providers` for it
    would re-probe every provider a second."""
    return {"memory": meminfo.gauge()}


def _providers(session: Session, scope: str = "") -> dict[str, Any]:
    """The model picker: every reachable provider's models under their
    provider captions, and the panel's field rows. An api key's VALUE is
    never sent — only where it comes from.

    Every CONFIGURED provider, not only the ones otaku ships a client
    for: a section somebody added by hand is a provider they play on,
    and the terminal lists those after the supported ones, by name. A picker
    that hides the model the session is using is a picker with no way
    back to it.

    `scope` is which slice to ask — the terminal's own two-phase rule
    (its picker opens on the providers on this machine and lets the rest
    answer after): "local" probes and lists everything but the catalogs,
    "cloud" only those — the hosted ones and the generic provider,
    whose url could point anywhere — a provider's name only it (the
    one-provider refresh a Test connection is), "" the whole set."""
    providers = api_providers.supported(session)
    catalogs = {p.id for p in providers if p.locality is not Locality.LOCAL}
    everyone = {p.id for p in providers} | api_providers.configured(session)
    if scope == "local":
        asked = everyone - catalogs
    elif scope == "cloud":
        asked = catalogs
    elif scope:
        asked = {scope} & everyone
        if not asked:
            # A name-scope that names nothing: the spec's 404, not an
            # empty inventory pretending the provider exists.
            raise NotFound(f"no provider {scope!r}")
    else:
        asked = everyone
    rows, reachable = api_providers.get_providers(session, skip=everyone - asked)
    # Seeded from what is ASKED, not from what answered: a provider
    # whose server is down is exactly the one a reader opens the picker
    # to fix, and `get_providers` returns only the reachable.
    models: dict[str, list[dict[str, Any]]] = {name: [] for name in asked}
    # What each provider can do, for the cards that answered; a card
    # whose provider did not carries null.
    abilities: dict[str, dict[str, Any] | None] = dict.fromkeys(asked)
    for row in rows:
        abilities[row.id] = {
            "tokenizer": row.capabilities.tokenizer,
            "prompt_cache": row.capabilities.prompt_cache,
            "model_management": row.capabilities.model_management,
            "supported_params": [p for p in PARAMETERS if p in row.capabilities.supported_params],
        }
        models.setdefault(row.id, []).extend(
            {
                "name": model.name,
                "loaded": model.state is ModelState.LOADED
                if row.capabilities.model_management
                else True,
                "size": format_size(model.size) if model.size else "",
                "max_context_catalogue": format_context(model.max_context_catalogue)
                if model.max_context_catalogue
                else "",
                "max_context_loaded": format_context(model.max_context_loaded)
                if model.max_context_loaded
                else "",
                "capabilities": _model_capabilities(model),
            }
            for model in row.models
        )
    known = {p.id: p for p in providers}
    # The supported providers in their own order, then whatever else is configured,
    # by name — the terminal's `order.get(name, len(order))` — and only
    # the slice that was asked: a scoped answer carries no card it did
    # not probe, so the page never draws a lamp nobody checked. Each card
    # SAYS its position too, because the page asks in two phases and the
    # order runs across both: the generic provider, asked in the second
    # phase, sits between the engines on this machine and the catalogs.
    rank = {p.id: i for i, p in enumerate(providers)}
    named = [p.id for p in providers if p.id in asked]
    named += sorted(name for name in models if name not in known)
    return {
        "current": session.full_model_name,
        # The one machine fact a picker needs: loading a model is what
        # fills a machine up. Said below both frontends, so the terminal's
        # gauge and the page's are one sentence (`backend.meminfo`).
        "memory": meminfo.gauge(),
        "providers": [
            _card(
                session,
                name,
                known.get(name),
                rank.get(name, len(rank)),
                abilities,
                models,
                reachable,
            )
            for name in named
        ],
    }


def _model_capabilities(model: ModelInfo) -> dict[str, Any] | None:
    """What the model can do, as the page reads it: each flag as the
    provider states it, null where it cannot say; the efforts in the
    wire's order, weakest to strongest; null altogether where the
    provider says nothing of the model."""
    caps = model.capabilities
    if caps is None:
        return None
    return {
        "vision": caps.vision,
        "audio": caps.audio,
        "reasoning": [e for e in EFFORTS if e in caps.reasoning]
        if caps.reasoning is not None
        else None,
        "text_completion": caps.text_completion,
        "structured_output": caps.structured_output,
    }


def _card(
    session: Session,
    name: str,
    provider: SupportedProvider | None,
    order: int,
    abilities: dict[str, dict[str, Any] | None],
    models: dict[str, list[dict[str, Any]]],
    reachable: set[str] | frozenset[str],
) -> dict[str, Any]:
    """One provider as the picker draws it. A configured section that is
    not one of the supported providers has no roster entry to describe
    it, so it speaks for itself: its own name, what its config says, and
    no idea where it runs."""
    section = api_providers.section(session, name)
    source = api_providers.key_source(session, name)
    return {
        "id": name,
        "label": provider.label if provider is not None else name,
        "order": order,
        "locality": (provider.locality if provider is not None else Locality.UNKNOWN).value,
        "connected": name in reachable,
        "url": section.url,
        "key_source": source.value if source is not None else None,
        "capabilities": abilities.get(name),
        "models": models.get(name, []),
    }


def settings(session: Session) -> dict[str, Any]:
    """The /set family as values — what each knob stands at, and where
    it persists, which is a real distinction: the toggles are
    session-wide, the parameters per model."""
    return {
        "think": session.think or THINK_UNSET,
        # What the model takes, in the ladder's one shared order
        # (`api.settings.think_levels`) — the segmented control draws it,
        # never re-sorts it.
        "think_levels": api_settings.think_levels(session),
        "verbose": session.verbose,
        "autocorrect": session.autocorrect,
        "notification": session.notification,
        # Tokens the prompt may use at most; 0 = the model's own max context.
        "max_context": session.max_context_setting,
        "model": session.model,
        # The parameters the provider in use reads
        # (`api.settings.parameter_names`), never one its wire would drop.
        "parameters": [
            {
                "name": name,
                "value": str(session.params.get(name, "")),
                "type": PARAMETERS[name].kind.__name__,
                # The bounds the setter holds a value to, null where none:
                # the page's placeholder, its sign rule and its mark.
                "min": PARAMETERS[name].low,
                "max": PARAMETERS[name].high,
            }
            for name in api_settings.parameter_names(session)
        ],
    }


def context(session: Session) -> dict[str, Any]:
    """The next request: the shape the window diagram is drawn from, the
    summary, and one part per message. Nothing is rewritten — the page
    draws the role markers as the design draws them, around the report's
    own text."""
    report = reports.context(session)
    shape = report.shape
    return {
        # The derived numbers ride along: `asdict` sees fields only, and
        # the page draws kept/total/used exactly as the terminal says them.
        "shape": asdict(shape)
        | {"kept": shape.kept, "total_tokens": shape.total_tokens, "used": shape.used},
        "lede": report.summary,
        "parts": [asdict(part) for part in report.parts],
    }


def _usage(session: Session, raw: str = "") -> dict[str, Any]:
    """What the tokens were spent on, as the table's rows — and every
    scope the report can be asked for, because the page draws a tab per
    scope and needs them all to draw any.

    Refused — no story, nothing recorded, an argument that is not "all"
    — reaches the page as the sentence it is, BESIDE the scopes rather
    than instead of them: the scope that refused is the one the reader
    is on, and the other tab is how they get out of it."""
    scopes = [{"key": key, "label": label} for key, label in reports.USAGE_SCOPES]
    try:
        report = reports.usage(session, raw)
    except Refused as refusal:
        # Marked the way every decline is, even beside its scopes: one
        # refusal grammar, so the page reads a flag and never a wording.
        return {"notice": str(refusal), "refused": True, "scopes": scopes}
    return {
        "scope": report.scope,
        "scopes": scopes,
        # `label` rides along: what a purpose is CALLED is decided below
        # both frontends (`reports.USAGE_PURPOSES`), never here.
        "rows": [asdict(row) | {"label": row.purpose_label} for row in report.rows],
        "requests": report.requests,
        "prompt_tokens": report.prompt_tokens,
        "completion_tokens": report.completion_tokens,
        "cached_tokens": report.cached_tokens,
        "total_tokens": report.total_tokens,
        # The figures said in a sentence, and how much of the spend
        # nobody asked for — the report's own words, not the page's.
        "note": report.note,
    }


def _money(money: Money | None) -> dict[str, Any] | None:
    """One amount on the wire: the figure as a STRING (a decimal is not
    a float and must not become one crossing JSON), its currency, and
    the rendering both frontends print."""
    if money is None:
        return None
    return {"amount": str(money.amount), "currency": money.currency, "text": str(money)}


def _balance(session: Session, ask: Ask) -> dict[str, Any]:
    """What each cloud account has left, as Money — and the note that
    stands where a figure would be for an account nobody has a key for
    or one that would not answer. `total` is everything on account when
    one currency covers every row, and null when it does not: adding
    across currencies is a conversion, and otaku has no rate.

    `?probe=none` returns the roster without asking any network (keyed
    rows carry an empty note — not asked yet): the page paints the whole
    slip from it, then fills the figures from one plain read."""
    report = reports.balances(session, probe=ask.query.get("probe") != "none")
    return {
        "rows": [
            {
                "provider": row.provider,
                "label": row.label,
                "money": _money(row.money),
                "note": row.note,
                "value": row.value,
            }
            for row in report.rows
        ],
        "total": _money(report.total),
        # What the story on screen spends — the report's own sentence.
        "note": report.note,
    }


def _info(session: Session) -> dict[str, Any]:
    """Everything otaku knows about this session, in the blocks the
    report is built from — labelled facts, or the sentence that stands
    where a block's facts would be."""
    return {
        "sections": [
            {"rows": [list(row) for row in section.rows], "note": section.note}
            for section in reports.info(session).sections
        ]
    }


def _export_document(session: Session, story_id: int | None = None) -> dict[str, str]:
    """A story as one Markdown document, and the name to save it under
    — `story_id` for one that is not open, so the browser can export
    without landing on it first. Writing the file is the page's: a
    browser saves where the reader says, and the server never learns
    where."""
    return {
        "name": api_transfer.export_name(session, story_id),
        "text": api_transfer.export(session, story_id),
    }


# ---------- what a screen writes ----------
#
# One entry in ROUTES each, and nothing else: the table at the foot of
# this module is the whole list, and a function here that is not in it
# would be a door nobody can open.


def _undo(session: Session) -> str:
    """Take back the trailing exchange — the popped rows are the page's
    cue to redraw, and the sentence says what happened. Nothing to take
    is a refusal like every other, so the page reads the `refused` flag
    the server marks them with and never the wording — which is COPIED:
    the terminal refuses with the same "Nothing to undo."
    (`chat.bindings`, its home), and the backend hands back only the
    rows."""
    popped = api_play.undo(session)
    if not popped:
        raise Refused("Nothing to undo.")
    return f"Took back the last exchange ({len(popped)} messages)."


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
    return Created(said, _at(session), {"story": session.story_id})


def _at(session: Session) -> str:
    """Where the story the session just landed in now lives."""
    return f"/api/stories/{session.story_id}"


def _edit_message(session: Session, ask: Ask) -> str:
    api_stories.edit_message(session, ask.id("message"), ask.need("text"), story_id=ask.id("story"))
    return "Message edited."


def _edit_scene(session: Session, ask: Ask) -> str:
    """A scene's own two fields. The arc (`history`) is derived and has
    no write — correcting the entries rebuilds it."""
    return _edit_lore(session, ask, "scene", {"title": "scene-title", "summary": "scene-summary"})


def _edit_character(session: Session, ask: Ask) -> str:
    return _edit_lore(session, ask, "character", {"description": "description", "card": "card"})


def _edit_journal(session: Session, ask: Ask) -> str:
    """A journal record's entry. The state is the extractor's own — like
    both histories it has no write; correcting the entry is what moves
    the record."""
    return _edit_lore(session, ask, "record", {"entry": "entry"})


def _edit_lore(session: Session, ask: Ask, target: str, kinds: dict[str, FieldKind]) -> str:
    """One corrected row of the memory. The PATH says which row — the
    story, then a scene, a character, or a journal record — and the body
    says which of its fields; `api_lore.edit` takes all three as one
    address and CHECKS the row is that story's.

    Fields are applied in order and each answers for itself: a body
    carrying two (the page sends one, the spec allows more) must not
    let a later refusal claim nothing was saved when an earlier field
    was — so a mixed outcome says both, and only an outcome with no
    save at all is a refusal."""
    said: list[str] = []
    refused: list[str] = []
    for name, kind in kinds.items():
        if name in ask.body:
            try:
                # `need`, not a bare str(): a null title would be stored
                # as the literal word "None" (`Ask.need`).
                said.append(
                    api_lore.edit(session, kind, ask.id(target), ask.need(name), ask.id("story"))
                )
            except Refused as refusal:
                refused.append(str(refusal))
    if not said and refused:
        raise Refused(" ".join(refused))
    if not said:
        raise Refused(f"Nothing to change — send one of {', '.join(kinds)}.")
    return " ".join(said + refused)


def _merge(session: Session, ask: Ask) -> str:
    return api_lore.merge_by_id(session, ask.id("character"), int(ask.body["into"]))


def _switch_model(session: Session, ask: Ask) -> str:
    return api_providers.switch_model(session, ask.need("provider"), ask.need("model"))


def _load_model(session: Session, ask: Ask) -> str:
    provider, model = ask.params["provider"], ask.params["model"]
    if ask.body["loaded"]:
        api_providers.load(session, provider, model)
        return f"Loaded {model}."
    api_providers.unload(session, provider, model)
    return f"Unloaded {model}."


def _save_provider(session: Session, ask: Ask) -> str:
    """A provider's url or api key, whichever the body names. The key's
    value goes IN here and never comes back out through any read. An
    EMPTY value is the terminal's Del: the url or the stored key is
    forgotten, file and session both."""
    said = ""
    provider = ask.params["provider"]
    for attr in ("url", "api_key"):
        if attr in ask.body:
            # `need`, not a bare str(): a null here would write the
            # literal url "None" into providers.toml (`Ask.need`).
            value = ask.need(attr)
            word = attr.replace("_", " ")
            if value.strip():
                warning = api_providers.save_field(session, provider, attr, value)
                said = warning or f"Saved {word} for {provider}."
            else:
                warning = api_providers.clear_field(session, provider, attr)
                said = warning or f"Cleared {word} for {provider}."
    if not said:
        raise Refused("Nothing to change — send a url or an api_key.")
    return said


def _set_knob(session: Session, ask: Ask) -> str:
    """One session-wide knob. The value crosses as given — JSON's one
    boolean spelling translated back into the command words — and each
    setter parses its own: the shapes are the backend's, so the page
    never learns what `think` accepts."""
    setter = _KNOBS.get(ask.params["setting"])
    if setter is None:
        raise Refused(f"Unknown setting {ask.params['setting']!r}.")
    value = ask.body["value"]
    return setter(session, "on" if value is True else "off" if value is False else str(value))


_KNOBS: dict[str, Callable[[Session, str], str]] = {
    "think": api_settings.set_think,
    "verbose": api_settings.set_verbose,
    "autocorrect": api_settings.set_autocorrect,
    "notification": api_settings.set_notification,
    "max_context": api_settings.set_max_context,
}


def _set_param(session: Session, ask: Ask) -> str:
    return api_settings.set_parameter_value(session, ask.params["name"], ask.need("value"))


def _reset_param(session: Session, ask: Ask) -> str:
    return api_settings.set_parameter_value(session, ask.params["name"], "reset")


def _record_history(session: Session, ask: Ask) -> str:
    """One submitted composer line into the ↑/↓ history — what the
    terminal prompt does at its own door, fired by the page beside every
    submission (blanks and immediate repeats are the session's to skip).
    Nothing to say back: the submission itself is the event."""
    session.record_history(ask.need("line"))
    return ""


# ---------- the flows that span two requests ----------
#
# A file arrives, is read and vetted, and only then lands — with a
# question in between. `Pending` keeps what waits between the halves,
# because a request is over before the next one starts.

# How long a card waits on its persona answer before the next prepare
# sweeps it — long enough to read the question, short enough that a
# cancelled import is not still in memory an hour later.
_CARD_PATIENCE = 600.0


class Pending:
    """What the web holds between requests on behalf of the session —
    the state a terminal keeps on its call stack, forced off it here
    because a request ends before the question it opened is answered.

    Two things span requests by design: the extraction passes the page
    polls — keyed by STORY, because an import can start a pass while a
    forced one still runs, and a single slot would deliver one story's
    report as another's — and cards read and vetted, waiting on their
    persona answer, keyed because two tabs preparing at once must not
    swap each other's: the answer would bind the wrong character to the
    wrong persona.

    Touched on the session's one thread (every flow runs there), with
    one exception: `extraction` is also READ from a handler thread by
    the poll, which is safe because a dict get against a dict set is
    atomic under the GIL and a run's `poll` is channel-safe by
    contract."""

    def __init__(self) -> None:
        self.extraction: dict[int, WorkerRun] = {}
        self._cards: dict[str, tuple[PreparedCard, float]] = {}

    def hold_card(self, prepared: PreparedCard) -> str:
        """Keep a prepared card for its persona answer; returns the
        token the page hands back. A persona ask that was cancelled,
        reloaded past, or closed never comes back for its card — nothing
        else would ever drop it, so each new one sweeps what has gone
        stale."""
        now = time.monotonic()
        for token, (_, asked) in list(self._cards.items()):
            if now - asked > _CARD_PATIENCE:
                del self._cards[token]
        token = secrets.token_hex(8)
        self._cards[token] = (prepared, now)
        return token

    def take_card(self, token: str) -> PreparedCard | None:
        """The card a token names, forgotten in the taking — None for a
        page that asked twice, or a reload between the two halves."""
        held = self._cards.pop(token, None)
        return held[0] if held else None


def _new_story(session: Session, ask: Ask, pending: Pending) -> Created:
    """A story, made. Empty with a title (or nothing), or read out of an
    uploaded document — an otaku export, a SillyTavern chat, or plain
    text. Content-shaped by contract: a path over HTTP would name a file
    on the SERVER, so the page sends what it read.

    The extraction pass the memoryless shapes start is kept where a
    forced pass is kept, so the page polls one place for both; a native
    export arrives with its memory and runs none — `watching` says so,
    which is what stops the page polling for a report never coming."""
    document: Any = ask.body.get("import")
    if not document:
        said = api_stories.new(session, ask.text("title"))
        # The id it made, so a page that needs a story to address — a
        # premise written before the first message — can address this one
        # without reading `Location` back apart.
        return Created(said, _at(session), {"story": session.story_id})
    landed = api_transfer.import_file(session, str(document["text"]), str(document["name"]))
    # Only an import that STARTED a pass takes a slot — and its own
    # story's slot, so a forced pass the page is polling on another
    # story keeps its run and its report.
    if landed.extraction is not None and session.story_id is not None:
        pending.extraction[session.story_id] = landed.extraction
    return Created(
        " ".join(landed.notices),
        _at(session),
        # And whether a pass is running on it, which is what stops the
        # page polling for a report that is never coming.
        {"story": session.story_id, "watching": landed.extraction is not None},
    )


def _prepare_card(session: Session, ask: Ask, pending: Pending) -> Created:
    """Everything before the persona ask. The prepared card is OPAQUE —
    it waits in `Pending` and the page hands back only the token and the
    answer. Bytes-shaped for the same reason as an import: the web
    uploads what it read, because a path over HTTP would name a file on
    the SERVER."""
    prepared = api_cards.prepare(
        session,
        base64.b64decode(ask.need("data")),
        ask.need("name"),
        ask.text("rename"),
    )
    token = pending.hold_card(prepared)
    return Created(
        "",
        f"/api/cards/{token}",
        {
            "token": token,
            "card": {
                "name": prepared.card.name,
                "notes": list(prepared.notes),
                "tokens": prepared.block_tokens,
                "large": prepared.large,
                "persona": api_cards.remembered_persona(session),
            },
        },
    )


def _add_card(session: Session, ask: Ask, pending: Pending) -> dict[str, Any]:
    """Everything after it. Nothing waiting — a page that asked twice, or
    a reload between the two halves — is an expected decline, and refuses
    the way every other one does."""
    prepared = pending.take_card(ask.params["token"])
    if prepared is None:
        raise Refused("No card is waiting.")
    return {"notice": api_cards.add(session, prepared, ask.need("persona")).report}


def _stop_extract(session: Session, ask: Ask, pending: Pending) -> dict[str, Any]:
    """Give up on the pass the page forced — the door Ctrl+C opens in
    the terminal, which is the only other way to leave one. Nothing
    half-done commits; already-closed scenes stay. Nothing running is an
    expected decline, refused like every other: a pass can finish
    between the reader asking and this arriving, and an automatic pass
    is the worker's own — the page never started it and cannot end it."""
    running = pending.extraction.get(ask.id("story"))
    if running is None or running.poll() is not None:
        raise Refused("No pass is running.")
    return {"notice": running.cancel()}


def _start_extract(session: Session, ask: Ask, pending: Pending) -> dict[str, Any]:
    """Force an extraction pass now and keep the run, so the page can
    ask for its report without ever holding the session's one thread. A
    second pass would overwrite the run the page is polling, and the
    first one's report would never be read."""
    story = ask.id("story")
    running = pending.extraction.get(story)
    if running is not None and running.poll() is None:
        return {"notice": "A pass is already running.", "watching": True}
    pending.extraction[story] = api_lore.extract(session)
    return {"notice": "Extracting lore from the recent messages…", "watching": True}


# ---------- the reply stream ----------


def play(session: Session, line: str) -> Iterator[PlayEvent]:
    """One submitted story line. Validation is eager, so a Refused
    reaches the caller before any of this is streamed — and before
    anything is recorded."""
    return api_play.submit(session, line)


def regenerate(session: Session) -> Iterator[PlayEvent]:
    """Sibling the standing reply and stream the fresh take. Eager like
    `play`: without a model, or with nothing to regenerate, it refuses
    before anything is dropped."""
    return api_play.regenerate(session)


def event(happened: PlayEvent) -> dict[str, Any]:
    """One play event as the page reads it. The union is closed and the
    match is exhaustive: a new event kind is a type error here, not a
    silence on the wire."""
    match happened:
        case Recorded():
            # `note` is the record's own dim line (a /roll's dice); ""
            # rides along so the shape never depends on the turn.
            return {"type": "recorded", "turn": _turn(happened.message), "note": happened.note}
        case Reasoning():
            return {"type": "reasoning", "text": happened.text}
        case Text():
            return {"type": "text", "text": happened.text}
        case Declined():
            return {"type": "declined", "reason": happened.reason}
        case Failed():
            return {"type": "failed", "reason": happened.reason}
        case Done():
            return {"type": "done", "stats": happened.stats}


# ---------- shared shapes ----------


def _journal(journal: Journal) -> dict[str, Any]:
    """One character's line in one scene — the thing BOTH lenses show,
    which is why it carries the two sides it hangs between. `id` is what
    a correction addresses; a scene draws these under its own heading, a
    character draws the same rows from the other side."""
    return {
        "id": journal.id,
        "scene": journal.scene_id,
        "character": journal.character_id,
        "entry": journal.entry,
        "state": journal.state,
        # When the extractor last wrote it — an audit stamp, display alone.
        "updated_at": journal.updated_at,
    }


def _turn(message: Message) -> dict[str, Any]:
    """One stored turn as the page reads it: the body, and the facts
    recorded with it — the kind the language stored it as, who answered
    an assistant turn and with what template, and the speaker the
    extractor named (None until its pass reads the turn, which is a
    state the reader pane draws as pending)."""
    return {
        "id": message.id,
        "role": message.role,
        "body": message.body,
        "kind": message.kind,
        "speaker": message.speaker,
        "provider": message.provider,
        "model": message.model,
        "template": message.template,
    }


# ---------- the whole surface, in one table ----------


# One route's work: the session, and the request taken apart. What it
# returns is what the page gets — a payload, a bare sentence the server
# wraps as a notice, or a `Created` when it made something.
_Route = Callable[[Session, Ask], Any]

# A flow's work is the same, plus the state that outlives one request.
# Taking `Pending` as an argument is what says so: a route that spans two
# requests cannot be mistaken for one that does not.
_Flow = Callable[[Session, Ask, Pending], Any]

# Every path the page may ask for whose work begins and ends inside the
# request, by METHOD and template. The method IS the lane (`web.server`):
# a GET only reads the session and is answered in the gaps of a streaming
# reply, and anything else takes the thread in turn. `{name}` in a
# template is a path parameter, and reaches the handler as
# `ask.params[name]`. The paths that span two requests are `FLOWS`, below.
#
# Four paths are NOT here, because none of them touch the session's
# thread: `/api/status` and `/api/watch` never do, `GET .../extraction`
# reads a run's own channel-safe poll, and the two that PLAY answer with
# a stream rather than a payload. The server holds those itself.
ROUTES: dict[tuple[str, str], _Route] = {
    # Playing
    ("GET", "/api/play"): lambda session, ask: {"messages": _turns(session)},
    ("DELETE", "/api/play/last"): lambda session, ask: _undo(session),
    ("GET", "/api/play/syntax"): lambda session, ask: syntax(),
    ("GET", "/api/cast"): lambda session, ask: cast(session),
    ("GET", "/api/history"): lambda session, ask: {"lines": _history(session)},
    ("POST", "/api/history"): _record_history,
    # All stories
    ("GET", "/api/stories"): lambda session, ask: {
        "stories": stories(session, ask.query.get("q", ""))
    },
    ("GET", "/api/stories/{story}"): lambda session, ask: story(session, ask.id("story")),
    ("DELETE", "/api/stories/{story}"): _delete_story,
    ("PUT", "/api/stories/{story}/title"): _title,
    ("POST", "/api/stories/{story}/fork"): _fork,
    ("PATCH", "/api/stories/{story}/messages/{message}"): _edit_message,
    ("GET", "/api/stories/{story}/export"): lambda session, ask: _export_document(
        session, ask.id("story")
    ),
    # Inside a story
    ("PUT", "/api/stories/{story}/premise"): _premise,
    ("PATCH", "/api/stories/{story}/scenes/{scene}"): _edit_scene,
    ("PATCH", "/api/stories/{story}/characters/{character}"): _edit_character,
    ("PUT", "/api/stories/{story}/characters/{character}/merge"): _merge,
    ("PATCH", "/api/stories/{story}/journals/{record}"): _edit_journal,
    # Models
    ("GET", "/api/providers"): lambda session, ask: _providers(session, ask.query.get("scope", "")),
    ("GET", "/api/providers/{provider}"): lambda session, ask: _providers(
        session, ask.params["provider"]
    ),
    ("PATCH", "/api/providers/{provider}"): _save_provider,
    ("PATCH", "/api/providers/{provider}/models/{model}"): _load_model,
    ("PUT", "/api/session/model"): _switch_model,
    ("PUT", "/api/session/model/parameters/{name}"): _set_param,
    ("DELETE", "/api/session/model/parameters/{name}"): _reset_param,
    ("GET", "/api/machine"): lambda session, ask: _memory(session),
    # The session
    ("GET", "/api/session"): lambda session, ask: facts(session),
    ("PUT", "/api/session/head"): _head,
    ("GET", "/api/session/context"): lambda session, ask: context(session),
    ("GET", "/api/session/info"): lambda session, ask: _info(session),
    ("GET", "/api/balance"): _balance,
    ("GET", "/api/usage"): lambda session, ask: _usage(session, ask.query.get("scope", "")),
    # Settings
    ("GET", "/api/settings"): lambda session, ask: settings(session),
    ("PUT", "/api/settings/{setting}"): _set_knob,
}

# The five paths whose work outlives the request that started it — a
# document that lands and starts a pass, a card waiting on its persona
# answer, a pass the page polls. Matched exactly as `ROUTES` is, and
# answered on the same lane; the third argument is the whole difference.
FLOWS: dict[tuple[str, str], _Flow] = {
    # All stories
    ("POST", "/api/stories"): _new_story,
    # Extraction
    ("POST", "/api/stories/{story}/extraction"): _start_extract,
    ("DELETE", "/api/stories/{story}/extraction"): _stop_extract,
    # Import card
    ("POST", "/api/cards"): _prepare_card,
    ("PUT", "/api/cards/{token}"): _add_card,
}
