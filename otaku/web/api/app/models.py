"""A provider's models, as the picker asks for them: the models it serves
and one loaded or unloaded, and a model's own settings — its thinking
level and its parameters."""

from typing import Any

from otaku.backend.api import UNCHANGED, Api, Unchanged
from otaku.web.api._request import Ask, Route, changed, plain


def _update_settings(api: Api, ask: Ask) -> dict[str, Any]:
    """A model's thinking level — a rung, off or on, "unset", or a budget
    in tokens, as a text or a number — and its parameters, an object of
    a value (the kind's own, or as a reader types it) or null, which
    unsets one. What either may take is the backend's to refuse."""
    think: str | Unchanged = UNCHANGED
    if "think" in ask.body:
        value = ask.body["think"]
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise TypeError("think is not a level or a number of tokens")
        think = str(value)
    parameters = ask.body.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        raise TypeError("parameters is not an object")
    return changed(
        api.providers.models.settings.update(
            ask.params["provider"], ask.params["model"], think=think, parameters=parameters
        )
    )


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/providers/{provider}/models"): lambda api, ask: {
        "items": plain(api.providers.models.list(ask.params["provider"]))
    },
    ("PATCH", "/api/providers/{provider}/models/{model}"): lambda api, ask: changed(
        api.providers.models.update(
            ask.params["provider"], ask.params["model"], loaded=ask.flag("loaded")
        )
    ),
    ("GET", "/api/providers/{provider}/models/{model}/settings"): lambda api, ask: plain(
        api.providers.models.settings.get(ask.params["provider"], ask.params["model"])
    ),
    ("PATCH", "/api/providers/{provider}/models/{model}/settings"): _update_settings,
}
