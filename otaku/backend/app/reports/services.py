"""The facts the installation's reports are made of, gathered: the tokens
spent, what otaku knows of the model and the run, and what each cloud
account has left. The reports themselves — the facts as a frontend is
answered them, and their text — are the interface's (`api.app.reports`); a
story's next request is the story's (`story.context.services`)."""

from dataclasses import dataclass

from otaku.backend.app.core.services import App
from otaku.backend.app.models import services as models
from otaku.backend.errors import NotFound
from otaku.formatting import (
    LABEL_WIDTH,
    Money,
    flatten,
    format_context,
    format_size,
    pretty_path,
    truncate_label,
)
from otaku.providers import ALL_CLIENTS, Locality, ModelState
from otaku.store.ops.records import UsageTotal

# A block of `info`: labelled facts, in order.
Rows = tuple[tuple[str, str], ...]

# What stands where a balance would be. Not a zero and not a blank: a row
# that is there because the provider is, with nothing to report on it yet.
_NO_KEY = "no key set"
_NO_ANSWER = "\u2014"


@dataclass(frozen=True)
class Account:
    """A cloud account and what it has left: its section's name, what to
    call it, the figure (None where it would not say) and why there is
    none ("" while it was not asked)."""

    provider: str
    label: str
    money: Money | None
    note: str


# ---------- the tokens spent ----------


def get_usage(app: App, story_id: int | None = None) -> list[UsageTotal]:
    """Tokens spent on a story — on every story where none is named —
    grouped by what they were spent on, then by provider and model.
    Raises NotFound when there is nothing to report."""
    totals = app._store.usage.get_totals(story_id)
    if not totals:
        raise NotFound(
            "No recorded usage yet." if story_id is None else "No recorded usage for this story."
        )
    return totals


# ---------- what otaku knows ----------


def get_info(app: App, story_id: int | None = None) -> tuple[Rows, Rows | None, Rows]:
    """Everything otaku knows about the active model and this run, in
    three blocks of labelled facts — the state dir, the model (None
    without one), the app — best-effort: network-backed fields are
    silently skipped. Without a model only that block is missing — the
    state dir, the story and the parameters are the app's own, and
    reporting them needs no provider."""
    state = (("State dir", pretty_path(app._paths.root)),)
    model = _model_info(app) if app._client(app._model()) is not None else None
    return state, model, _app_rows(app, story_id)


# ---------- what each account has left ----------


def get_balance(app: App, *, probe: bool = True) -> tuple[list[Account], str]:
    """Every provider with an account to bill, and what each says it has
    left. EVERY one: a provider with no key, a wrong key or an outage
    keeps its row with a dash, because a report of only what answered
    cannot be told apart from one that found nothing — and the row with
    the dash is usually the one the reader came to look at. The cloud
    catalogs are asked concurrently; a provider on this machine has
    no account.

    `probe=False` asks no network at all and returns the ROSTER — each
    keyed row with no figure and an EMPTY note, meaning "not asked yet"
    rather than "would not answer": the page paints the whole slip from
    it and fills the figures from one probed report. The terminal asks
    plainly and gets everything in one wait. Answered with the sentence
    that says what the story on screen spends."""
    registry = app._providers
    configured = set(registry.list())

    def account(provider: str) -> Account | None:
        client = registry.get(provider)
        if client is None or client.locality is not Locality.REMOTE:
            return None  # a provider on this machine has no account to ask
        # What to CALL it: the provider's own caption — "OpenRouter", not
        # "openrouter".
        named = client.label
        # An account nobody has a key for was never asked: that is a
        # different fact from an account that would not answer, and the
        # reader can act on one of them.
        if client.auth.key_source is None:
            return Account(provider, named, None, _NO_KEY)
        if not probe:
            return Account(provider, named, None, "")
        try:
            money = client.balance(timeout=5.0)
        except Exception:
            money = None
        return Account(provider, named, money, "" if money else _NO_ANSWER)

    rows = [row for row in registry.map(account) if row]
    # A cloud provider otaku ships a client for and nobody has configured
    # is still an account a reader may be about to open: it belongs in
    # the list, with nothing in it.
    rows += [
        Account(id, cls.label, None, _NO_KEY)
        for id, cls in ALL_CLIENTS.items()
        if cls.locality is Locality.REMOTE and id not in configured
    ]
    if not rows:
        raise NotFound("No cloud providers.")
    # What the story on screen costs, in one sentence. A balance is only
    # ever read as "can I afford to keep playing", and the answer depends
    # on what this story is PLAYING on — which a list of accounts does
    # not say.
    model = app._model()
    client = app._client(model)
    playing = model.provider if model is not None else ""
    if client is None or model is None:
        spending = "Paid providers are charged only when you play on one."
    elif client.locality_of(model.name) is Locality.REMOTE:
        spending = f"This story runs on {playing}, and every reply is billed to that account."
    else:
        spending = (
            f"This story runs on {playing}, which spends nothing. "
            "Paid providers are charged only when you switch to one."
        )
    return rows, spending


# ---------- internals ----------


# A model's load state as the Loaded row says it.
_STATE_WORDS = {
    ModelState.LOADED: "yes",
    ModelState.UNLOADED: "no",
    ModelState.LOADING: "loading",
    ModelState.UNKNOWN: "unknown",
}


def _model_info(app: App) -> tuple[tuple[str, str], ...]:
    """The active model's block of `info` — the caller checked a model
    is active."""
    model = app._model()
    client = app._client(model)
    assert client is not None and model is not None
    # The registry's copy, not a snapshot: a URL or key edited in the
    # picker panel shows here immediately.
    config = client.config
    out = [("Backend", f"{client.id} ({config.url})"), ("Model", model.name)]
    if client.auth.key_source is not None:
        out.append(("Auth", "api_key configured"))
    # The model's own row. Load state and size only where loading is a
    # real state (a cloud catalog serves everything statically); the
    # capabilities from any provider that reports them, a catalog included
    # — that one costs a full catalog fetch, and /info is the place to
    # pay it. The generic provider reports none of these, wherever its
    # url points, so it is not asked.
    row = client.models.get(model.name) if client.locality is not Locality.UNKNOWN else None
    if row is not None and client.locality_of(model.name) is Locality.LOCAL:
        out.append(("Loaded", _STATE_WORDS[row.state]))
        if row.size:
            out.append(("Size", format_size(row.size)))
    # One row for the context: the model's own size where known, and —
    # only when the loaded instance was given a different one — what a
    # request actually gets, said first: "64K (of 128K native)".
    catalogue = format_context(row.context_length if row else None)
    loaded = format_context(row.context_window if row else None)
    if catalogue and loaded and loaded != catalogue:
        out.append(("Max context", f"{loaded} (of {catalogue} native)"))
    elif catalogue or loaded:
        out.append(("Max context", catalogue or loaded))
    # What the model can do, as the provider says it: how its thinking
    # is set, then the capabilities it has, in the dataclass's order —
    # "unknown" where the provider could not say.
    caps = row.capabilities if row is not None else None
    out.append(("Reasoning", models.reasoning_words(caps)))
    out.append(("Capabilities", models.capability_words(caps)))
    if config.keep_alive:
        out.append(("Keep-alive", str(config.keep_alive)))
    if client.capabilities.prompt_cache:
        # Displayed here, decided in providers.toml — the keep_alive
        # pattern: behaviour keys are read in /info, edited in the file.
        out.append(("Prompt cache", config.prompt_cache or "5m"))
    return tuple(out)


def _app_rows(app: App, story_id: int | None) -> tuple[tuple[str, str], ...]:
    """The app's block of `info` — what is loaded, not what answers."""
    out = []
    listing = app._store.stories.get_listing(story_id) if story_id is not None else None
    label = flatten(listing.label) if listing is not None else ""
    if label:
        out.append(("Story", truncate_label(label, LABEL_WIDTH)))
    out.append(("Messages", str(listing.num_messages if listing is not None else 0)))
    # Where lore extraction reads the story: the model saved for it, or
    # the one the model block above names.
    model, pin = app._model(), app._extraction_pin()
    if pin is not None:
        out.append(("Extraction", pin.spec))
    elif model is not None:
        out.append(("Extraction", "the story model"))
    # No premise row: a premise is a DOCUMENT, not a fact about the
    # app — it is as long as a reader made it, and `/system` reports
    # it on its own, at whatever length that is. A report of one-line
    # facts is the wrong place to print an imported lorebook.
    saved = app._model_settings(model.name).params if model is not None else {}
    if model is not None and saved:
        # Each as a reader types it, and marked where the provider in
        # use does not read it — kept for the model, sent elsewhere.
        held = models.get_model_settings(app, model.provider, model.name)
        reaching = {p.name for p in held.parameters if p.supported}
        out.append(
            (
                "Parameters",
                ", ".join(
                    f"{k} = {models.parameter_text(v)}"
                    + ("" if k in reaching else f" (not read by {model.provider})")
                    for k, v in saved.items()
                ),
            )
        )
    return tuple(out)
