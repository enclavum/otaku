"""The installation's settings, typed, carried out — the twin of
`api.app.settings`: the /set switches and the max context."""

from collections.abc import Callable

from otaku.terminal.api.app import settings as api_settings
from otaku.terminal.chat.chat import Chat

# ---------- the rows ----------


HANDLERS: dict[str, Callable[[Chat, str], str | None]] = {
    "/set verbose": lambda chat, raw: api_settings.set_verbose(chat.api, raw),
    "/set autocorrect": lambda chat, raw: api_settings.set_autocorrect(chat.api, raw),
    "/set notification": lambda chat, raw: api_settings.set_notification(chat.api, raw),
    "/set max_context": lambda chat, raw: api_settings.set_max_context(chat.api, raw),
}
