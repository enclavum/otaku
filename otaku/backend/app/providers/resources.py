"""The providers as a frontend reaches them: the picker's listing and a
provider's own section (its url and key). The work is the domain's
(`app.providers.services`); a provider's models and a model's own
settings are `api.app.models`', the model in use and the one lore
extraction asks the app's (`api.app`)."""

from __future__ import annotations

from otaku.backend.app.core.services import App
from otaku.backend.app.models.resources import ModelsResource
from otaku.backend.app.models.schemas import as_model
from otaku.backend.app.models.services import EFFORT_LEVELS, PARAMETERS
from otaku.backend.app.providers import services as domain_providers
from otaku.backend.app.providers.schemas import ListedProvider, Provider, ProviderListing
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged, any_thread
from otaku.providers import ALL_CLIENTS, Locality


class ProvidersResource(Resource):
    """/providers — every provider the panel shows, and each one's own
    section."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.models = ModelsResource(app)

    @any_thread
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
