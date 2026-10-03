"""The integrations: the services otaku asks for something other than a
completion — the web search engines, one module each — and the one
registry; beside them, otaku's own support desk, a service with no key
and so no section (`support`), and the sky's own facts, asked of no
service (`astronomy`: the moon's phase, the nearest solstice or equinox).

A section of integrations.toml IS its integration
(`ALL_INTEGRATIONS`, keyed by section name, as the providers' sections
are their engines); a section under any other name is passed over, and
the launch says so. The type of a section is `settings.integrations`',
as is the file's one CHOICE: which engine web search asks, decided by
the key's own shape when one is saved (`search_engine_of_key`).

A new integration is a module, a subclass of `Integration` (or of
`SearchEngine`, for one that searches) and its name here.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from otaku.integrations.astronomy import moon_phase, turning_point
from otaku.integrations.base import (
    Integration,
    IntegrationError,
    SearchEngine,
    SearchResult,
)
from otaku.integrations.exa import Exa
from otaku.integrations.support import MAX_SUPPORT_MESSAGE, post_support_request
from otaku.integrations.tavily import Tavily
from otaku.settings.integrations import IntegrationConfig, IntegrationSettings

ALL_INTEGRATIONS: dict[str, type[Integration]] = {Tavily.id: Tavily, Exa.id: Exa}

__all__ = [
    "ALL_INTEGRATIONS",
    "MAX_SUPPORT_MESSAGE",
    "Integration",
    "IntegrationConfig",
    "IntegrationError",
    "Integrations",
    "SearchEngine",
    "SearchResult",
    "default_sections",
    "moon_phase",
    "post_support_request",
    "search_engine_of_key",
    "turning_point",
]


@dataclass(frozen=True)
class Integrations:
    """The integrations as BOUND — one instance per configured integration
    the registry knows, by id, each holding its url and key — and the
    engine web search asks, chosen by the key saved (None while none
    is). What a tool runs on reads off the session (`Tool.run`). Not the
    settings: those are `settings.integrations.IntegrationSettings`,
    bound into this by `from_settings` at launch; a key saved rebinds
    one engine and moves the choice (`backend.api.integrations`). What
    the settings named that could not be bound is reported, not
    dropped in silence: `ignored`, the configured names no integration
    answers to, and `unknown_web_search_engine`, a choice naming no
    search engine, as written ("" when the choice is good or none)."""

    bound: Mapping[str, Integration] = field(default_factory=dict)
    web_search_engine: SearchEngine | None = None
    ignored: tuple[str, ...] = ()
    unknown_web_search_engine: str = ""

    @classmethod
    def from_settings(cls, settings: IntegrationSettings) -> "Integrations":
        """The settings bound: each configured integration the registry
        knows as an instance, the rest `ignored`; the choice resolved to
        its bound engine where it names one."""
        bound = {
            name: ALL_INTEGRATIONS[name](entry)
            for name, entry in settings.integrations.items()
            if name in ALL_INTEGRATIONS
        }
        ignored = tuple(name for name in settings.integrations if name not in ALL_INTEGRATIONS)
        chosen = settings.web_search_engine
        engine = bound.get(chosen) if chosen else None
        if isinstance(engine, SearchEngine):
            return cls(bound, engine, ignored)
        return cls(bound, None, ignored, unknown_web_search_engine=chosen)


def default_sections() -> dict[str, IntegrationConfig]:
    """Every integration's section as founded, in the registry's order —
    what first run writes, and what a later launch founds where missing,
    so the file shows where a key goes."""
    return {cls.id: cls.default_section() for cls in ALL_INTEGRATIONS.values()}


def search_engine_of_key(key: str) -> type[SearchEngine]:
    """The search engine a key belongs to, told from its shape: the
    engine whose `key_prefix` it starts with, else the one whose keys
    carry no mark — so a key is saved with no engine named."""
    engines = [cls for cls in ALL_INTEGRATIONS.values() if issubclass(cls, SearchEngine)]
    marked = (cls for cls in engines if cls.key_prefix and key.startswith(cls.key_prefix))
    return next(marked, None) or next(cls for cls in engines if not cls.key_prefix)
