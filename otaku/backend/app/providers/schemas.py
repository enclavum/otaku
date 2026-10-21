"""What `app.providers.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.app.models.schemas import Model

# Re-exported: the two fields of a section a panel edits.
from otaku.backend.app.providers.services import ProviderField as ProviderField
from otaku.backend.base import Schema
from otaku.providers import (
    KeySource,
    Locality,
    ProviderCapabilities,
)


class ProviderListing(Schema):
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


class ListedProvider(Schema):
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


class Provider(Schema):
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
