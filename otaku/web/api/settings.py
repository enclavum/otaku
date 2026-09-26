"""The settings slip, as the page reads and moves it: every knob of the
/set family and what it takes, a model's parameters, and a tool's
prompt in prompts.toml."""

from collections.abc import Callable
from typing import Any

from otaku.backend.api import settings as api_settings
from otaku.backend.session import PARAMETERS, THINK_UNSET, Refused, Session
from otaku.web.api.request import Ask, Route


def settings(session: Session) -> dict[str, Any]:
    """The /set family as values — what each knob stands at, and where
    it persists, which is a real distinction: the toggles are
    session-wide, the parameters per model."""
    choices = api_settings.think_choices(session)
    return {
        "think": session.think or THINK_UNSET,
        # What the model takes, in the one shared order
        # (`api.settings.think_choices`) — the segmented control draws
        # the levels, never re-sorts them, and a field takes the budget
        # where one is.
        "think_levels": choices.levels,
        "think_budget": choices.budget,
        "verbose": session.verbose,
        "autocorrect": session.autocorrect,
        "notification": session.notification,
        # Tokens the prompt may use at most; 0 = the model's own max context.
        "max_context": session.max_context_setting,
        "idle_seconds": api_settings.idle_seconds(session),
        "model": session.model,
        # Every parameter /set knows, in its order, each saying whether
        # it reaches the model in use (`api.settings.parameter_read`):
        # the page draws the unsupported ones closed, never silently
        # dropped from the slip.
        "parameters": [
            {
                "name": name,
                "supported": api_settings.parameter_read(session, name),
                "value": api_settings.parameter_text(session.params[name])
                if name in session.params
                else "",
                # The page's three kinds: the stop list is a text field
                # there, and the setter reads the text.
                "type": "str" if PARAMETERS[name].kind is list else PARAMETERS[name].kind.__name__,
                # The bounds the setter holds a value to — the provider's
                # own where it states them — null where none: the page's
                # placeholder, its sign rule and its mark.
                "min": api_settings.parameter_bounds(session, name)[0],
                "max": api_settings.parameter_bounds(session, name)[1],
            }
            for name in PARAMETERS
        ],
    }


def _set_knob(session: Session, ask: Ask) -> str:
    """One session-wide knob. The value crosses as given — JSON's one
    boolean spelling translated back into the command levels — and each
    setter parses its own: the shapes are the backend's, so the page
    never learns what `think` accepts."""
    setter = _KNOBS.get(ask.params["setting"])
    if setter is None:
        raise Refused(f"Unknown setting {ask.params['setting']!r}.")
    value = ask.body["value"]
    return setter(session, "on" if value is True else "off" if value is False else str(value))


_KNOBS: dict[str, Callable[[Session, str], str]] = {
    "think": api_settings.set_think,
    "verbose": api_settings.set_verbose,
    "autocorrect": api_settings.set_autocorrect,
    "notification": api_settings.set_notification,
    "max_context": api_settings.set_max_context,
}


def _set_param(session: Session, ask: Ask) -> str:
    return api_settings.set_parameter_value(session, ask.params["name"], ask.need("value"))


def _reset_param(session: Session, ask: Ask) -> str:
    return api_settings.set_parameter_value(session, ask.params["name"], "reset")


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/settings"): lambda session, ask: settings(session),
    ("PUT", "/api/settings/{setting}"): _set_knob,
    ("PUT", "/api/session/model/parameters/{name}"): _set_param,
    ("DELETE", "/api/session/model/parameters/{name}"): _reset_param,
    # A tool's prompt: prompts.toml's, edited in place.
    ("GET", "/api/prompts/{tool}"): lambda session, ask: {
        "text": api_settings.get_tool_prompt(session, ask.params["tool"])
    },
    ("PUT", "/api/prompts/{tool}"): lambda session, ask: api_settings.set_tool_prompt(
        session, ask.params["tool"], ask.need("text")
    ),
}
