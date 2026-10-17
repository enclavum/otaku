"""The providers as a frontend reaches them: the picker's listing and a
provider's own section (its url and key). The work is the domain's
(`domain.app.providers`); a provider's models and a model's own
settings are `api.app.models`', the model in use and the one lore
extraction asks the app's (`api.app`)."""

from __future__ import annotations

from dataclasses import dataclass

from otaku.backend.api._writes import UNCHANGED, Changed, Unchanged
from otaku.backend.api.app.models import Model, ModelsResource, as_model
from otaku.backend.domain.app import App
from otaku.backend.domain.app import providers as domain_providers
from otaku.backend.domain.app.models import EFFORT_LEVELS, PARAMETERS

# Re-exported: the two fields of a section a panel edits.
from otaku.backend.domain.app.providers import ProviderField as ProviderField
from otaku.providers import (
    ALL_CLIENTS,
    KeySource,
    Locality,
    ProviderCapabilities,
)


class ProvidersResource:
    """/providers — every provider the panel shows, and each one's own
    section."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.models = ModelsResource(app)

    def list(self, *, scope: str = "", probe: bool = True) -> ProviderListing:
        """Every provider in the panel's order, those in `scope` asked for
        their models — "" all of them, "local" all but the hosted
        catalogs, "cloud" the catalogs and the generic provider (whose
        url could point anywhere), a provider's name that one alone; a
        provider no section is written for has nothing to ask. `probe`
        False asks nobody: the panel's vocabulary, and the models in use
        as last listed. Safe from any thread — it reads the files and asks
        the providers, never the store — so a picker lists each provider
        on a thread of its own. Raises NotFound for a name no provider
        answers to."""
        app = self._app
        asked = domain_providers.list_providers(app, scope=scope, probe=probe)
        rows = {row.id: row for row in asked.rows}
        listed = []
        for name in asked.order:
            cls = ALL_CLIENTS.get(name)
            row = rows.get(name)
            config = domain_providers.get_provider(app, name)[0] if cls is not None else None
            listed.append(
                ListedProvider(
                    id=name,
                    label=cls.label if cls is not None else name,
                    locality=cls.locality if cls is not None else Locality.UNKNOWN,
                    configured=name in asked.configured,
                    url=config.url if config is not None else "",
                    asked=name in asked.asked,
                    answered=row is not None,
                    failure=asked.failures.get(name),
                    key_source=(
                        row.key_source
                        if row is not None
                        else cls.key_source(config)
                        if cls is not None and config is not None
                        else None
                    ),
                    capabilities=row.capabilities if row is not None else None,
                    models=tuple(as_model(app, model) for model in row.models) if row else (),
                )
            )
        return ProviderListing(
            providers=tuple(listed),
            parameters=tuple(PARAMETERS),
            effort_levels=EFFORT_LEVELS,
        )

    def get(self, provider: str) -> Provider:
        """A provider as its section stands, nothing asked of its server —
        the panel's fields. Raises NotFound for a name no supported
        provider answers to: a section is its provider's name."""
        config, configured = domain_providers.get_provider(self._app, provider)
        cls = ALL_CLIENTS[provider]
        return Provider(
            id=provider,
            label=cls.label,
            locality=cls.locality,
            configured=configured,
            url=config.url,
            key_source=cls.key_source(config),
        )

    def update(
        self,
        provider: str,
        *,
        url: str | Unchanged = UNCHANGED,
        api_key: str | Unchanged = UNCHANGED,
    ) -> Changed[Provider]:
        """A provider's url or api key saved, or forgotten where given as
        "": sealed (a key), written into providers.toml in ONE write (a
        missing section is founded — how a cloud provider is added), live
        in the registry at once. Raises Refused for a name no supported
        provider answers to, nothing to change, a key that could not be
        sealed, and a write the file would not take."""
        said = domain_providers.update_provider(
            self._app,
            provider,
            url=None if isinstance(url, Unchanged) else url,
            api_key=None if isinstance(api_key, Unchanged) else api_key,
        )
        return Changed(self.get(provider), said)


@dataclass(frozen=True)
class ProviderListing:
    """The picker's one query answered: every provider in the panel's
    order — the supported ones in theirs, then any other configured by
    name — and the /set vocabulary's order, every parameter and every
    effort rung, which a provider's and a model's lists are read in. The
    model in use and the one lore extraction asks are the app's
    (`api.app`)."""

    providers: tuple[ListedProvider, ...]
    parameters: tuple[str, ...]
    effort_levels: tuple[str, ...]

    @property
    def reachable(self) -> frozenset[str]:
        """The providers that answered."""
        return frozenset(provider.id for provider in self.providers if provider.answered)


@dataclass(frozen=True)
class ListedProvider:
    """One provider in the panel: its id (which names its section), its
    label (the project's own spelling) and where it runs (a catalog's
    url is fixed and its models billed; the generic provider's url could
    name either, so it says unknown); whether a section is written and
    the url it holds, whether it was asked and answered — the package's sentence for why
    where it was asked and could not (a dead server, a rejected key, an
    error status) — where the key it sends comes from (the key itself
    never comes back out), and, where it answered, what it can do and
    the models it serves."""

    id: str
    label: str
    locality: Locality
    configured: bool
    url: str
    asked: bool
    answered: bool
    failure: str | None
    key_source: KeySource | None
    capabilities: ProviderCapabilities | None
    models: tuple[Model, ...]


@dataclass(frozen=True)
class Provider:
    """One provider as its section stands — the one written, or the
    supported provider's default where none is yet: the supported
    provider it is, whether a section is written, its url, and where the
    key it sends comes from (the key itself never comes back out)."""

    id: str
    label: str
    locality: Locality
    configured: bool
    url: str
    key_source: KeySource | None
