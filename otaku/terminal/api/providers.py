"""The providers as the terminal reaches them — the twin of
`backend.app.providers`: everything it answers, re-exported, and one
of a section's two fields saved, or forgotten (`update_field`)."""

from otaku.backend.api import UNCHANGED, Api
from otaku.backend.app.providers.schemas import ListedProvider as ListedProvider
from otaku.backend.app.providers.schemas import Provider as Provider
from otaku.backend.app.providers.schemas import ProviderField as ProviderField
from otaku.backend.app.providers.schemas import ProviderListing as ProviderListing


def update_field(api: Api, name: str, attr: ProviderField, value: str) -> None:
    """A field saved, or forgotten where `value` is "" — the other field
    left as it stands."""
    api.providers.update(
        name,
        url=value if attr == "url" else UNCHANGED,
        api_key=value if attr == "api_key" else UNCHANGED,
    )
