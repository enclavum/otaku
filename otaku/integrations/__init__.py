"""The integrations: what otaku asks for something other than a
completion, one class each, all in the one registry (`ALL_INTEGRATIONS`)
— web search, whose one key its section of integrations.toml holds
(`WebSearch`); otaku's own support desk (`SupportDesk`); and the sky's
own facts (`Astronomy`). The last two are not `configurable`: the file
gets no section for them.

A new integration is a module, a subclass of `Integration` and its place
in the registry.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar, cast

from otaku.integrations.astronomy import Astronomy
from otaku.integrations.base import Integration, IntegrationError
from otaku.integrations.support import SupportDesk
from otaku.integrations.web_search import SearchResult, WebSearch
from otaku.settings.integrations import IntegrationConfig, IntegrationSettings

ALL_INTEGRATIONS: tuple[type[Integration], ...] = (WebSearch, Astronomy, SupportDesk)

_T = TypeVar("_T", bound=Integration)

__all__ = [
    "ALL_INTEGRATIONS",
    "Astronomy",
    "Integration",
    "IntegrationConfig",
    "IntegrationError",
    "Integrations",
    "SearchResult",
    "SupportDesk",
    "WebSearch",
    "default_sections",
]


@dataclass(frozen=True)
class Integrations:
    """Every integration as the app holds it, by id: each bound to its
    section of integrations.toml, or to the one it is founded with where
    the file has none. A key saved rebinds one (`backend.app.integrations`)."""

    by_id: Mapping[str, Integration]

    @classmethod
    def bind(cls, settings: IntegrationSettings) -> "Integrations":
        sections = settings.integrations
        return cls({c.id: c(sections.get(c.id) or c.default_section()) for c in ALL_INTEGRATIONS})

    def get(self, kind: type[_T]) -> _T:
        """The integration of class `kind`."""
        return cast(_T, self.by_id[kind.id])


def default_sections() -> dict[str, IntegrationConfig]:
    """Every configurable integration's section as founded — what first
    run writes, and what a later launch founds where missing, so the file
    shows where a key goes."""
    return {c.id: c.default_section() for c in ALL_INTEGRATIONS if c.configurable}
