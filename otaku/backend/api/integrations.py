"""The integrations as a frontend reaches them: ONE box for the web
search key in either frontend, no engine named. The key's own shape
says which engine it belongs to (`integrations.search_engine_of_key`):
it goes sealed into that engine's section of integrations.toml, the
engine's id into the file's `[web_search]` choice — what
`Session.integrations` carries as `web_search_engine` — and both live
in the session at once. The value goes IN here and never comes back out
through any read: `web_search` says which engine is chosen and whether
it holds a key."""

from dataclasses import dataclass, replace

from otaku.backend.session import Refused, Session
from otaku.encryption import SealedError, seal
from otaku.formatting import toml_scalar
from otaku.integrations import IntegrationConfig, search_engine_of_key
from otaku.settings.integrations import (
    ENGINE_KEY,
    WEB_SEARCH_SECTION,
    section_block,
    web_search_block,
)
from otaku.settings.migrations import surgery


@dataclass(frozen=True)
class WebSearch:
    """Where web search stands: the engine chosen — its section name
    and its label, "" for none — and whether it holds a key."""

    engine: str
    label: str
    has_key: bool


def web_search(session: Session) -> WebSearch:
    engine = session.integrations.web_search_engine
    if engine is None:
        return WebSearch("", "", False)
    return WebSearch(engine.id, engine.label, bool(engine.api_key))


def set_web_search_key(session: Session, key: str) -> str:
    """The web search key, whichever engine's it is: sealed into that
    engine's section (founded keyless where a hand edit dropped it),
    the engine made the choice, both live in the session at once. ""
    forgets the chosen engine's key and the choice. Returns the
    confirmation, or the warning when the file could not be written
    (the session still took it)."""
    key = key.strip()
    if not key:
        return _clear_web_search_key(session)
    cls = search_engine_of_key(key)
    try:
        sealed = seal(
            key, key_file=session._paths.config_key_file, service=session._paths.keychain_service
        )
    except SealedError as e:
        raise Refused(f"Save failed: {e}") from e
    standing = session.integrations
    bound = standing.bound.get(cls.id)
    url = bound.url if bound is not None else cls.endpoint
    choice = f"{ENGINE_KEY} = {toml_scalar(cls.id)}"
    written = surgery.update_settings_file(
        session._paths.integrations_file,
        session._paths.config_backups_dir,
        [
            surgery.ensure_section(cls.id, section_block(IntegrationConfig(cls.id, url))),
            surgery.set_key(cls.id, "api_key", f"api_key = {toml_scalar(sealed)}"),
            surgery.ensure_section(WEB_SEARCH_SECTION, web_search_block("")),
            surgery.set_key(WEB_SEARCH_SECTION, ENGINE_KEY, choice),
        ],
    )
    engine = cls(IntegrationConfig(cls.id, url, key))
    session._integrations = replace(
        standing, bound={**standing.bound, cls.id: engine}, web_search_engine=engine
    )
    if not written:
        # The session took the value, the file did not — say so, or the
        # next launch silently forgets what the box confirmed.
        return "Saved for this session only — integrations.toml could not be written."
    return f"Saved web search key for {cls.label}."


def _clear_web_search_key(session: Session) -> str:
    """Forget the chosen engine's key and the choice — file and session
    both, or NEITHER: a clear that cannot reach the file keeps the
    session copy too, and says so."""
    engine = session.integrations.web_search_engine
    if engine is None:
        raise Refused("No web search key to clear.")
    written = surgery.update_settings_file(
        session._paths.integrations_file,
        session._paths.config_backups_dir,
        [
            surgery.set_key(engine.id, "api_key", 'api_key = ""'),
            surgery.set_key(WEB_SEARCH_SECTION, ENGINE_KEY, f'{ENGINE_KEY} = ""'),
        ],
    )
    if not written:
        return "Not forgotten — integrations.toml could not be written."
    standing = session.integrations
    keyless = type(engine)(IntegrationConfig(engine.id, engine.url))
    session._integrations = replace(
        standing, bound={**standing.bound, engine.id: keyless}, web_search_engine=None
    )
    return f"Cleared web search key for {engine.label}."
