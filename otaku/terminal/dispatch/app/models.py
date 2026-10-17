"""A model's own settings, typed, carried out — the twin of
`api.app.models`: `/set think` and `/set parameter` on the model in use."""

from collections.abc import Callable

from otaku.terminal.api.app import models as api_models
from otaku.terminal.chat.chat import Chat

# ---------- the rows ----------


HANDLERS: dict[str, Callable[[Chat, str], str | None]] = {
    "/set think": lambda chat, raw: api_models.set_think(chat.api, raw),
    "/set parameter": lambda chat, raw: api_models.set_parameter(chat.api, raw),
}
