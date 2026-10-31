"""The providers as the app holds them: the model in use and the one
pinned for lore extraction, switched or pinned by name; every provider
in a scope asked for its models; a provider's own section — its url and
key — and the models it loads.

A provider's edits write providers.toml surgically, seal an api key
first (encryption's sealing plane), and update the running registry in
the same call, so an edit is live at once; an edit the file would not
take is refused, and nothing changed.
"""

import contextlib
from dataclasses import dataclass, replace
from typing import Literal

from otaku.backend.app.core.services import App
from otaku.backend.errors import Invalid, NotFound, Unavailable
from otaku.encryption import SealedError, seal
from otaku.formatting import toml_key, toml_scalar
from otaku.providers import (
    ALL_CLIENTS,
    Locality,
    ProviderConfig,
    ProviderFailure,
    ProviderInfo,
)
from otaku.settings.migrations import PROMPT_CACHE_ROW, surgery
from otaku.settings.state import ModelRef

# The two fields of a section a panel edits.
ProviderField = Literal["url", "api_key"]


@dataclass(frozen=True)
class Listing:
    """Every provider in a scope asked: the panel's order — the supported
    providers in their own, then any other configured by name — which
    are configured, which were asked, the answers of those that answered
    and, for each asked that could not, the package's sentence for why —
    a dead server, a rejected key, an error status."""

    order: tuple[str, ...]
    configured: frozenset[str]
    asked: frozenset[str]
    rows: tuple[ProviderInfo, ...]
    failures: dict[str, str]


# ---------- the models in use ----------


def switch_model(app: App, provider: str, model: str) -> str:
    """Play on `model` from now on, keeping the story (compare models on
    one prompt: switch, then regenerate), remembered as its provider
    lists it (`_as_listed`) — and the sentence, what the model's saved
    settings had to be told included (`App._make_current`). The model
    already in use says so. Raises Refused for a provider that is not
    configured, and a switch that could not be saved."""
    if provider not in app._providers.list():
        raise NotFound(f"Unknown provider {provider!r}.")
    wanted = ModelRef(provider, _as_listed(app, provider, model))
    if app._model() == wanted:
        return f"Already using {wanted.spec}."
    app._update_state_config(model=wanted.spec)
    notes = app._make_current()
    return " ".join([f"Switched to {wanted.spec}.", *notes])


def pin_extraction_model(app: App, provider: str, model: str) -> str:
    """A model of its own for lore extraction — any the picker lists, the
    one played with included: saved, it stays when that one changes.
    Remembered as its provider lists it (`_as_listed`). Raises Refused
    for a provider that is not configured."""
    if provider not in app._providers.list():
        raise NotFound(f"Unknown provider {provider!r}.")
    wanted = ModelRef(provider, _as_listed(app, provider, model))
    if app._extraction_pin() == wanted:
        return f"Lore extraction already uses {wanted.spec}."
    app._update_state_config(extraction_model=wanted.spec)
    return f"Lore extraction uses {wanted.spec}."


def unpin_extraction_model(app: App) -> str:
    """The model pinned for lore extraction forgotten: extraction asks the
    model played with again."""
    if app._extraction_pin() is None:
        return "Lore extraction already uses the model you play with."
    app._update_state_config(extraction_model="")
    return "Lore extraction uses the model you play with."


def _as_listed(app: App, provider: str, model: str) -> str:
    """`model` as `provider` lists it — Ollama lists a bare name under
    its ":latest" tag — so a model chosen is written down as it is: a
    local engine asked at once, as `App._make_current` asks it, anything
    else answering from what it last listed; as given where it has not
    listed the model."""
    client = app._providers.get(provider)
    if client is None:
        return model
    found = None
    if client.locality is Locality.LOCAL:
        with contextlib.suppress(Exception):
            found = client.models.get(model)
    else:
        found = client.models.cached(model)
    return found.name if found is not None else model


# ---------- the listing ----------


def list_providers(app: App, *, scope: str = "", probe: bool = True) -> Listing:
    """Every provider in `scope` asked for its models — "" all of them,
    "local" all but the hosted catalogs, "cloud" the catalogs and the
    generic provider (whose url could point anywhere), a provider's name
    that one alone; a provider no section is written for has nothing to
    ask. `probe` False asks nobody. Raises NotFound for a name no
    provider answers to."""
    registry = app._providers
    configured = frozenset(registry.list())
    catalogs = {cls.id for cls in ALL_CLIENTS.values() if cls.locality is not Locality.LOCAL}
    everyone = set(ALL_CLIENTS) | configured
    if scope == "local":
        asked = everyone - catalogs
    elif scope == "cloud":
        asked = catalogs
    elif scope:
        asked = {scope} & everyone
        if not asked:
            raise NotFound(f"No provider is named {scope}.")
    else:
        asked = everyone
    if not probe:
        asked = set()
    answers = registry.map(registry.info, [name for name in registry.list() if name in asked])
    return Listing(
        order=(*ALL_CLIENTS, *sorted(configured - set(ALL_CLIENTS))),
        configured=configured,
        asked=frozenset(asked),
        rows=tuple(answer for answer in answers if isinstance(answer, ProviderInfo)),
        failures={
            answer.id: answer.message for answer in answers if isinstance(answer, ProviderFailure)
        },
    )


# ---------- a provider's own section ----------


def get_provider(app: App, name: str) -> tuple[ProviderConfig, bool]:
    """A provider's section as it stands — the one written, or the
    supported provider's default where none is yet — and whether one is
    written; nothing asked of its server. Raises NotFound for a name no
    supported provider answers to: a section is its provider's name."""
    cls = ALL_CLIENTS.get(name)
    if cls is None:
        raise NotFound(f"No supported provider is named {name}.")
    written = app._providers.configs.get(name)
    return (written if written is not None else cls.autoconfigure()), written is not None


def update_provider(
    app: App, name: str, *, url: str | None = None, api_key: str | None = None
) -> str:
    """A provider's url or api key saved, or forgotten where given as "":
    sealed (a key), written surgically into providers.toml in ONE write
    (a missing section is founded — how a cloud provider is added), live
    in the registry at once. None leaves a field as it is. Raises
    Refused for a name no supported provider answers to, nothing to
    change, a key that could not be sealed, and a write the file would
    not take — nothing changed, the registry included. Answers the
    sentence that says what was done."""
    given: tuple[tuple[ProviderField, str | None], ...] = (("url", url), ("api_key", api_key))
    fields = {attr: value.strip() for attr, value in given if value is not None}
    if not fields:
        raise Invalid("Nothing to change — send a url or an api_key.")
    get_provider(app, name)  # a name no provider answers to is refused before anything
    written = app._providers.configs.get(name)
    config = written if written is not None else ALL_CLIENTS[name].autoconfigure()
    edits = []
    said = []
    for attr, value in fields.items():
        word = attr.replace("_", " ")
        if not value:
            said.append(f"Cleared {word} for {name}.")
            if getattr(config, attr):
                edits.append(surgery.set_key(name, attr, f'{attr} = ""'))
                config = replace(config, **{attr: ""})
            continue
        said.append(f"Saved {word} for {name}.")
        if attr == "url":
            value = value.rstrip("/")
            if written is not None and written.url == value:
                continue  # the url as it stands: saved, with nothing to write
            line = f"url = {toml_scalar(value)}"
        else:
            try:
                sealed = seal(
                    value, key_file=app._paths.config_key_file, service=app._paths.keychain_service
                )
            except SealedError as e:
                raise Unavailable(f"Save failed: {e}") from e
            line = f"api_key = {toml_scalar(sealed)}"
        edits.append(surgery.set_key(name, attr, line))
        config = replace(config, **{attr: value})
    if edits:
        # A provider not in providers.toml yet gets its section written
        # first — how a cloud provider is added deliberately. One that
        # honours cache breakpoints is founded with the prompt_cache row,
        # the line the upgrade migration writes, so the setting is
        # visible however the section got there. The name is QUOTED, as
        # every other writer of this file quotes it: a section header
        # built by concatenation is a way to write any row anywhere in
        # the file, and this one takes its name from a request.
        block = f"[{toml_key(name)}]\nurl = {toml_scalar(config.url)}\n" + 'api_key = ""'
        if ALL_CLIENTS[name].completion_class.can_mark_cache:
            block += "\n" + PROMPT_CACHE_ROW
        if not surgery.update_providers(
            app._paths.providers_file,
            app._paths.config_backups_dir,
            [surgery.ensure_section(name, block), *edits],
        ):
            saving = any(fields.values())
            raise Unavailable(
                "Not saved — providers.toml could not be written."
                if saving
                else "Not forgotten — providers.toml could not be written."
            )
        app._providers.update(config)
    return " ".join(said)


# ---------- the models a provider loads ----------
