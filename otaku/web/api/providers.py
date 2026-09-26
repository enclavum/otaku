"""Providers and models, as the picker asks for them: the listing in its
two phases, a provider's own, the machine's memory, a model switched,
loaded or unloaded, a provider's fields saved."""

from typing import Any

from otaku.backend import (
    Locality,
    ModelInfo,
    ModelState,
    meminfo,
)
from otaku.backend.api import providers as api_providers
from otaku.backend.api.providers import SupportedProvider
from otaku.backend.session import EFFORT_LEVELS, PARAMETERS, Refused, Session
from otaku.formatting import format_context, format_size
from otaku.web.api.reports import model_words
from otaku.web.api.request import Ask, NotFound, Route


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
    panel = api_providers.get_providers(session, skip=everyone - asked)
    rows, reachable = panel.rows, panel.reachable
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
                # Where the model is served: its own where it differs from
                # its provider's (Ollama's ollama.com rows), the provider's
                # otherwise — what a caption on the model reads.
                "locality": (model.locality or row.locality).value,
                # The info report's two rows on the model, in its words:
                # the page draws them under the picker as it draws /info.
                **model_words(model),
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
        # Under its listed name, which is how the page finds its row.
        "current": api_providers.listed_spec(session),
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
                panel.failures,
            )
            for name in named
        ],
    }


def _model_capabilities(model: ModelInfo) -> dict[str, Any] | None:
    """What the model can do, as the page reads it: each flag as the
    provider states it, null where it cannot say; the rungs in the
    wire's order, weakest to strongest; null altogether where the
    provider says nothing of the model."""
    caps = model.capabilities
    if caps is None:
        return None
    return {
        "vision": caps.vision,
        "audio": caps.audio,
        "supported_params": [p for p in PARAMETERS if p in caps.supported_params]
        if caps.supported_params is not None
        else None,
        "reasoning_efforts": [e for e in EFFORT_LEVELS if e in caps.reasoning_efforts]
        if caps.reasoning_efforts is not None
        else None,
        "reasoning_switch": caps.reasoning_switch,
        "reasoning_budget": caps.reasoning_budget,
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
    failures: dict[str, str],
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
        # Why not, in the package's sentence — a dead server, a rejected
        # key, an error status; "" when connected or never asked.
        "reason": failures.get(name, ""),
        "url": section.url,
        "key_source": source.value if source is not None else None,
        "capabilities": abilities.get(name),
        "models": models.get(name, []),
    }


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


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/providers"): lambda session, ask: _providers(session, ask.query.get("scope", "")),
    ("GET", "/api/providers/{provider}"): lambda session, ask: _providers(
        session, ask.params["provider"]
    ),
    ("PATCH", "/api/providers/{provider}"): _save_provider,
    ("PATCH", "/api/providers/{provider}/models/{model}"): _load_model,
    ("PUT", "/api/session/model"): _switch_model,
    ("GET", "/api/machine"): lambda session, ask: _memory(session),
}
