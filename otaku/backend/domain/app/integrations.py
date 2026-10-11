"""The integrations as the app holds them. Web search: its one key,
sealed into the web search section of integrations.toml and bound in the
app at once; which engine it belongs to is the integration's own
business (`integrations.WebSearch`). The support desk: a reader's message
to otaku's author, and the address to answer at where one is given, sent
through the site (`integrations.SupportDesk`) — checked here before
anything leaves, so a refusal is a sentence and never a mail the
endpoint drops or bounces."""

import re
from dataclasses import replace

from otaku import __version__
from otaku.backend.domain.app import App
from otaku.backend.errors import Invalid, Unavailable
from otaku.encryption import SealedError, seal
from otaku.formatting import toml_scalar
from otaku.integrations import IntegrationConfig, IntegrationError, SupportDesk, WebSearch
from otaku.settings.integrations import section_block
from otaku.settings.migrations import surgery

# What the support desk's endpoint takes for an address — its rule,
# copied (the otaku.sh repository's src/index.js). It would drop one
# that fails and keep the message; refused here instead, since an answer
# is why it was given.
_EMAIL = re.compile(r"[^@\s]+@[^@\s.]+\.[^@\s]+")
_MAX_EMAIL = 200  # the endpoint's cut


# ---------- web search ----------


def get_web_search(app: App) -> bool:
    """Whether web search holds a key — never the key."""
    return bool(app._integrations.get(WebSearch).api_key)


def update_web_search(app: App, api_key: str) -> str:
    """The web search key: sealed into its section (founded where a hand
    edit dropped it), live in the app at once; "" forgets it, and
    forgetting none says so — the sentence that says which. Raises
    Refused for a key that could not be sealed and a write the file would
    not take — nothing changed."""
    key = api_key.strip()
    if not key:
        return _forget_web_search_key(app)
    try:
        sealed = seal(key, key_file=app._paths.config_key_file, service=app._paths.keychain_service)
    except SealedError as e:
        raise Unavailable(f"Save failed: {e}") from e
    if not surgery.update_settings_file(
        app._paths.integrations_file,
        app._paths.config_backups_dir,
        [
            surgery.ensure_section(WebSearch.id, section_block(WebSearch.default_section())),
            surgery.set_key(WebSearch.id, "api_key", f"api_key = {toml_scalar(sealed)}"),
        ],
    ):
        raise Unavailable("Not saved — integrations.toml could not be written.")
    _rebind(app, key)
    return "Saved web search key."


def _forget_web_search_key(app: App) -> str:
    """Forget the key — file and app both, or neither."""
    if not app._integrations.get(WebSearch).api_key:
        return "No web search key to clear."
    if not surgery.update_settings_file(
        app._paths.integrations_file,
        app._paths.config_backups_dir,
        [surgery.set_key(WebSearch.id, "api_key", 'api_key = ""')],
    ):
        raise Unavailable("Not forgotten — integrations.toml could not be written.")
    _rebind(app, "")
    return "Cleared web search key."


def _rebind(app: App, key: str) -> None:
    """The app's web search bound anew with `key`, its url kept."""
    standing = app._integrations
    url = standing.get(WebSearch).url
    search = WebSearch(IntegrationConfig(WebSearch.id, url, key))
    app._integrations = replace(standing, by_id={**standing.by_id, WebSearch.id: search})


# ---------- the support desk ----------


def create_support_request(app: App, body: str, *, email: str = "") -> str:
    """Send `body`, and `email` where one is given, to otaku's author.
    Raises Refused for an empty or too long body, an address that is
    none, and a request that could not be sent."""
    text = body.strip()
    address = email.strip()
    if not text:
        raise Invalid("Nothing to send: write what you need help with.")
    if len(text) > SupportDesk.max_message:
        raise Invalid(
            f"The request is {len(text):,} characters long; it can be at most "
            f"{SupportDesk.max_message:,}."
        )
    if address and (len(address) > _MAX_EMAIL or not _EMAIL.fullmatch(address)):
        raise Invalid(f"{address} is not an email address.")
    desk = app._integrations.get(SupportDesk)
    try:
        desk.send(text, address, user_agent=f"otaku/{__version__}")
    except IntegrationError as e:
        raise Unavailable(f"The request could not be sent: {e}.") from e
    return "Your request was sent."
