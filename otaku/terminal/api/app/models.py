"""A model's own settings as the terminal reaches them — the twin of
`backend.api.app.models`: everything it answers, re-exported, and what
`/set think` and `/set parameter` mean on the model in use, with the
sentence each is answered with."""

from otaku.backend.api import NO_MODEL_HINT, Api, Conflict
from otaku.backend.api.app.models import Model as Model
from otaku.backend.api.app.models import ModelSettings as ModelSettings
from otaku.backend.api.app.models import ParameterSetting as ParameterSetting
from otaku.backend.api.app.models import ThinkChoices as ThinkChoices


def set_think(api: Api, raw: str) -> str:
    """`/set think [LEVEL]` on the model in use: bare reports where it
    stands and what the model takes; a level typed is the backend's to
    take or refuse."""
    provider, model = _in_use(api)
    settings = api.providers.models.settings
    if not raw.strip():
        held = settings.get(provider, model)
        return f"Think: {held.think_text}. Levels for this model: {held.choices.listed}."
    return settings.update(provider, model, think=raw.strip()).notice


def set_parameter(api: Api, raw: str) -> str:
    """`/set parameter [NAME [VALUE|reset]]` on the model in use: bare
    lists what is set, a bare name reports where it stands, "reset"
    unsets it, a value — the rest of the line, whole, since a stop
    string may hold spaces — sets it."""
    name, _, value_raw = raw.strip().partition(" ")
    provider, model = _in_use(api)
    settings = api.providers.models.settings
    held = settings.get(provider, model)
    # In the order the file holds them, each as a reader types it.
    saved = held.params
    if not name:
        if not saved:
            return "No parameters set."
        rows = "\n".join(f"  {key} = {text}." for key, text in saved.items())
        return f"Parameters:\n{rows}"
    if not model:
        raise Conflict(NO_MODEL_HINT)
    known = {parameter.name for parameter in held.parameters}
    if name not in known or value_raw.strip().lower() == "reset":
        # The unsetter refuses a name it does not know, naming what is.
        return settings.update(provider, model, parameters={name: None}).notice
    if not value_raw.strip():
        # Asking is not setting: the bare name shows where it stands.
        if name in saved:
            return f"{name} = {saved[name]}"
        return f"Parameter {name} is not set: the engine's own default applies."
    return settings.update(provider, model, parameters={name: value_raw}).notice


def _in_use(api: Api) -> tuple[str, str]:
    """The provider and bare name of the model in use; two "" for none."""
    model = api.app.get().model
    return (model.provider, model.name) if model is not None else ("", "")
