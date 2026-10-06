"""The integrations as a frontend reaches them: ONE box for the web
search key in either frontend. The key goes sealed into the web search
section of integrations.toml and lives in the session at once; which
engine it belongs to is the integration's own business
(`integrations.WebSearch`). The value goes IN here and never comes back
out through any read: `web_search` says whether a key is held."""

from dataclasses import dataclass, replace

from otaku.backend.session import Refused, Session
from otaku.encryption import SealedError, seal
from otaku.formatting import toml_scalar
from otaku.integrations import IntegrationConfig, WebSearch
from otaku.settings.integrations import section_block
from otaku.settings.migrations import surgery


@dataclass(frozen=True)
class WebSearchState:
    """Where web search stands: whether it holds a key."""

    has_key: bool


def web_search(session: Session) -> WebSearchState:
    return WebSearchState(bool(session.integrations.get(WebSearch).api_key))


def set_web_search_key(session: Session, key: str) -> str:
    """The web search key: sealed into its section (founded where a hand
    edit dropped it), live in the session at once. "" forgets it.
    Returns the confirmation, or the warning when the file could not be
    written (the session still took it)."""
    key = key.strip()
    if not key:
        return _clear_web_search_key(session)
    try:
        sealed = seal(
            key, key_file=session._paths.config_key_file, service=session._paths.keychain_service
        )
    except SealedError as e:
        raise Refused(f"Save failed: {e}") from e
    written = surgery.update_settings_file(
        session._paths.integrations_file,
        session._paths.config_backups_dir,
        [
            surgery.ensure_section(WebSearch.id, section_block(WebSearch.default_section())),
            surgery.set_key(WebSearch.id, "api_key", f"api_key = {toml_scalar(sealed)}"),
        ],
    )
    _rebind(session, key)
    if not written:
        # The session took the value, the file did not — say so, or the
        # next launch silently forgets what the box confirmed.
        return "Saved for this session only — integrations.toml could not be written."
    return "Saved web search key."


def _clear_web_search_key(session: Session) -> str:
    """Forget the key — file and session both, or NEITHER: a clear that
    cannot reach the file keeps the session copy too, and says so."""
    if not session.integrations.get(WebSearch).api_key:
        raise Refused("No web search key to clear.")
    written = surgery.update_settings_file(
        session._paths.integrations_file,
        session._paths.config_backups_dir,
        [surgery.set_key(WebSearch.id, "api_key", 'api_key = ""')],
    )
    if not written:
        return "Not forgotten — integrations.toml could not be written."
    _rebind(session, "")
    return "Cleared web search key."


def _rebind(session: Session, key: str) -> None:
    """The session's web search bound anew with `key`, its url kept."""
    standing = session.integrations
    url = standing.get(WebSearch).url
    search = WebSearch(IntegrationConfig(WebSearch.id, url, key))
    session._integrations = replace(standing, by_id={**standing.by_id, WebSearch.id: search})
