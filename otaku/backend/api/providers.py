"""The model and provider operations: switching, the picker's inventory,
and the panel's edits.

Panel saves write providers.toml surgically, seal api keys first
(encryption's sealing plane), and update the running registry in the same
call, so an edit is live at once; a write that could not land is SAID,
not swallowed.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from otaku.backend.session import Refused, Session
from otaku.encryption import SealedError, seal
from otaku.formatting import toml_key, toml_scalar
from otaku.providers import (
    ALL_CLIENTS,
    KeySource,
    Locality,
    ModelState,
    OpenAIClient,
    ProviderConfig,
    ProviderError,
    ProviderInfo,
)
from otaku.settings.migrations import PROMPT_CACHE_ROW, surgery

# The two fields of a section a panel edits — what `save_field` and
# `clear_field` take, so a frontend cannot name a third.
ProviderField = Literal["url", "api_key"]


def switch_model(session: Session, provider: str, model: str) -> str:
    """Switch for the rest of the session, keeping the context (compare
    models on one prompt: switch, then regenerate). Parameters follow the
    model; the switch is remembered. Returns the confirmation; raises
    Refused for an unknown provider or a no-op."""
    if provider not in session._providers_registry.list():
        raise Refused(f"Unknown provider {provider!r}.")
    if f"{provider}/{model}" == session.full_model_name:
        raise Refused(f"Already using {session.full_model_name}.")
    session._update_state(model=f"{provider}/{model}")
    session._reload_model_settings()
    session._read_model()
    return f"Switched to {session.full_model_name}."


def switch_spec(session: Session, raw: str) -> str:
    """The typed `/model PROVIDER/MODEL` form: owns the ONE split rule
    (the first slash — model names may carry more) and the refusal that
    lists the known providers, then wraps `switch_model`. Both frontends'
    chat boxes route here; the picker and the web PUT use the structured
    form."""
    known = session._providers_registry.list()
    head, _, rest = raw.strip().partition("/")
    if head not in known or not rest:
        names = ", ".join(known)
        raise Refused(f"Use PROVIDER/MODEL (providers: {names}), or /model with no args to pick.")
    return switch_model(session, head, rest)


def listed_spec(session: Session) -> str:
    """The session's model as its provider lists it — "provider/name",
    the spec a picker rows it under, which is where a picker's cursor
    lands on the model in use. Ollama lists a bare name under its
    ":latest" tag, and a spec typed without the tag must still be
    found. The remembered spelling where the provider has not listed
    the model, and "" without one."""
    client = session._client()
    found = client.models.cached(session.model) if client is not None else None
    if found is None:
        return session.full_model_name
    return f"{session.provider}/{found.name}"


def get_providers(
    session: Session, skip: set[str] | None = None
) -> tuple[list[ProviderInfo], set[str]]:
    """Every reachable provider with its models, plus the reachable set —
    the picker's one query; `skip` lets it fetch cloud catalogs after
    its screen is up."""
    registry = session._providers_registry
    asked = [name for name in registry.list() if name not in (skip or ())]
    rows = [row for row in registry.map(registry.info, asked) if row is not None]
    return rows, {row.id for row in rows}


@dataclass(frozen=True)
class SupportedProvider:
    """One provider otaku ships a client for, as the provider panel
    captions it — its id (which names its section), label (the
    project's own spelling), and where it runs (a catalog's url is
    fixed and its models billed; the generic provider's url could name
    either, so it says unknown)."""

    id: str
    label: str
    locality: Locality


def supported(session: Session) -> list[SupportedProvider]:
    """The supported providers in the panel's canonical order — the ONE
    source of the captions and the where-it-runs split, so no frontend
    keeps its own table."""
    return [SupportedProvider(cls.id, cls.label, cls.locality) for cls in ALL_CLIENTS.values()]


def configured(session: Session) -> set[str]:
    """The configured providers' names — what the panel's one-provider
    refresh skips everything but, and nothing more: the sections
    themselves come one at a time through `section`."""
    return set(session._providers_registry.list())


def loaded_models(session: Session, provider: str) -> set[str]:
    """Which of a provider's models are loaded right now — the picker's
    read-back after a load or unload, asked of that ONE provider with the
    listing's own patience (a server that just loaded a model is the
    slowest it ever is). Raises Refused when it cannot be reached: a
    refresh that failed quietly would leave the panel claiming the
    opposite of what just happened."""
    client = session._providers_registry.get(provider)
    if client is None:
        raise Refused(f"Unknown provider {provider!r}.")
    try:
        return {m.name for m in client.models.list() if m.state is ModelState.LOADED}
    except ProviderError as e:
        raise Refused(str(e)) from e


def section(session: Session, provider: str) -> ProviderConfig:
    """The provider's current section when configured, its autoconfigured
    default otherwise — what the panel shows either way. Raises Refused
    for a name no supported provider answers to: a section is its
    provider's name."""
    known = session._providers_registry.configs.get(provider)
    if known is not None:
        return known
    if provider not in ALL_CLIENTS:
        raise Refused(f"No supported provider is named {provider}.")
    return ALL_CLIENTS[provider].autoconfigure()


def key_source(session: Session, provider: str) -> KeySource | None:
    """Where the key the panel's field stands for comes from — the
    section's, the engine's environment variable, or none — so a field
    can say which without showing the value. Read off the section a
    save or a clear just moved, so the caption follows at once. Raises
    Refused as `section` does."""
    config = section(session, provider)
    return ALL_CLIENTS[provider].key_source(config)


def save_field(session: Session, provider: str, attr: ProviderField, value: str) -> str:
    """Save a url or api key: sealed (keys), written surgically into
    providers.toml (a missing section is founded — how a cloud provider
    is added), live in the registry at once. Returns "" or the warning
    when the file could not be written (the session still took it)."""
    value = value.strip()
    if not value:
        raise Refused("Nothing to save.")
    config = section(session, provider)
    if attr == "url":
        value = value.rstrip("/")
        line = f"url = {toml_scalar(value)}"
        updated = replace(config, url=value)
    else:
        try:
            sealed_value = seal(
                value,
                key_file=session._paths.config_key_file,
                service=session._paths.keychain_service,
            )
        except SealedError as e:
            raise Refused(f"Save failed: {e}") from e
        line = f"api_key = {toml_scalar(sealed_value)}"
        updated = replace(config, api_key=value)
    # A provider not in providers.toml yet gets its section written
    # first — this is how a cloud provider is added deliberately. A
    # provider that honours cache breakpoints is founded with the
    # prompt_cache row, the same line the upgrade migration writes, so
    # the setting is visible in the file however the section got there.
    # The name is QUOTED, as every other writer of this file quotes it
    # (`settings.providers`, `settings.models`): a section header built
    # by concatenation is a way to write any row anywhere in the file,
    # and this one takes its name from a request.
    block = f"[{toml_key(provider)}]\nurl = {toml_scalar(config.url)}\n" + 'api_key = ""'
    if provider in ALL_CLIENTS and ALL_CLIENTS[provider].completion_class.can_mark_cache:
        block += "\n" + PROMPT_CACHE_ROW
    written = surgery.update_providers(
        session._paths.providers_file,
        session._paths.config_backups_dir,
        [surgery.ensure_section(provider, block), surgery.set_key(provider, attr, line)],
    )
    session._providers_registry.update(updated)
    if not written:
        # The registry took the value, the file did not — say so, or the
        # next launch silently forgets what the panel confirmed.
        return "Saved for this session only — providers.toml could not be written."
    return ""


def clear_field(session: Session, provider: str, attr: ProviderField) -> str:
    """Forget a url or a stored key — file and session both, or NEITHER:
    a clear that cannot reach the file keeps the session copy too, and
    says so. A cleared url leaves the provider with nowhere to ask, so
    its rows go with it. Returns "" when forgotten (or there was nothing
    to forget)."""
    config = section(session, provider)
    if not getattr(config, attr):
        return ""  # nothing to clear — and the field is visibly bare
    written = surgery.update_providers(
        session._paths.providers_file,
        session._paths.config_backups_dir,
        [surgery.set_key(provider, attr, f'{attr} = ""')],
    )
    if not written:
        # Forgetting that does not reach the file is not forgetting: the
        # value stays — in the session too, so the panel stays honest.
        return "Not forgotten — providers.toml could not be written."
    cleared = replace(config, url="") if attr == "url" else replace(config, api_key="")
    session._providers_registry.update(cleared)
    return ""


def load(session: Session, provider: str, model: str) -> None:
    """Load on a provider that manages its models; blocks until the
    server answers. Every failure raises Refused with the curated
    sentence (not managed, the provider unreachable, the server's own
    error text) — no transport
    exception type ever crosses the boundary."""
    _perform(_managed(session, provider).models.load, model, provider)


def unload(session: Session, provider: str, model: str) -> None:
    _perform(_managed(session, provider).models.unload, model, provider)


def _managed(session: Session, provider: str) -> OpenAIClient:
    client = session._providers_registry.get(provider)
    if client is None:
        raise Refused(f"Unknown provider {provider!r}.")
    if not client.capabilities.model_management:
        raise Refused(f"{provider} cannot load or unload models.")
    return client


def _perform(action: Callable[[str], None], model: str, provider: str) -> None:
    """One load/unload call, its failures curated into Refused — shared
    by both doors, so the wording cannot fork. The sentence is the
    provider package's own, which names the provider."""
    try:
        action(model)
    except ProviderError as e:
        raise Refused(str(e)) from e
