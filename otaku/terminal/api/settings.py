"""The installation's settings as the terminal reaches them — the twin
of `backend.app.settings`: everything it answers, re-exported, and
what each typed `/set` word means — a switch's on and off, the max
context's count — with the sentence it is answered with."""

from collections.abc import Callable
from typing import Any

from otaku.backend.api import Api, Changed, Invalid
from otaku.backend.app.settings.schemas import Settings as Settings
from otaku.backend.app.settings.schemas import TerminalSettings as TerminalSettings
from otaku.backend.app.settings.schemas import WebFrontendSettings as WebFrontendSettings
from otaku.backend.app.settings.schemas import WebSettings as WebSettings

# The words a switch takes.
_ON = ("on", "true", "yes")
_OFF = ("off", "false", "no")


def set_verbose(api: Api, raw: str) -> str:
    """`/set verbose [on|off]`; bare reports where it stands."""
    current = api.settings.get().verbose
    return _switch(raw, "verbose", current, lambda on: api.settings.update(verbose=on))


def set_autocorrect(api: Api, raw: str) -> str:
    """`/set autocorrect [on|off]`; bare reports where it stands."""
    current = api.settings.get().autocorrect
    return _switch(raw, "autocorrect", current, lambda on: api.settings.update(autocorrect=on))


def set_notification(api: Api, raw: str) -> str:
    """`/set notification [on|off]`; bare reports where it stands."""
    current = api.settings.get().notification
    return _switch(raw, "notification", current, lambda on: api.settings.update(notification=on))


def set_serve(api: Api, raw: str) -> str:
    """`/set serve [on|off]`, the setting alone — the serving is the
    chat's; bare reports where it stands."""
    current = api.settings.terminal.get().terminal_serve
    return _switch(
        raw, "serve", current, lambda on: api.settings.terminal.update(terminal_serve=on)
    )


def set_max_context(api: Api, raw: str) -> str:
    """`/set max_context [TOKENS]` — a count, separators allowed, 0 = the
    model's own window; bare reports where it stands."""
    value = raw.strip().replace(",", "").replace("_", "")
    if not value:
        return f"Max context: {api.settings.get().context_limit_text}."
    try:
        tokens = int(value)
    except ValueError:
        raise Invalid(
            "Usage: /set max_context <tokens> — 0 = the model's own max context"
        ) from None
    return api.settings.update(context_limit=tokens).notice


def _switch(raw: str, name: str, current: bool, write: Callable[[bool], Changed[Any]]) -> str:
    """A switch's words: on and off in their spellings, bare reporting
    where it stands, anything else the usage line."""
    value = raw.strip().lower()
    if not value:
        return f"{name.capitalize()}: {'on' if current else 'off'}."
    if value in _ON:
        return write(True).notice
    if value in _OFF:
        return write(False).notice
    raise Invalid(f"Usage: /set {name} on|off")
