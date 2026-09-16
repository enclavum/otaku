"""The session's knobs: the /set family.

Values persist where they belong — state.toml for session-wide toggles,
models.toml per model (the parameters and the thinking level), and
never in the user-owned config, with ONE
exception: `max_context` LIVES in config.toml's [context] beside
head_messages and min_tail_messages (docs/context_design.md's home for it),
so /set max_context edits that file surgically — the picker's
provider-field saves are the precedent — rather than shadowing it from
a second file. Every operation takes the raw argument text and parses
it itself; every one returns the confirmation to show and raises
Refused for what it declines.
"""

from dataclasses import replace

from otaku.backend.session import (
    EFFORTS,
    NO_MODEL_HINT,
    PARAMETERS,
    THINK_MENU,
    Refused,
    Session,
)
from otaku.settings import models as models_file
from otaku.settings import row
from otaku.settings.migrations import surgery
from otaku.settings.models import THINK_KEY, THINK_UNSET

_ON = ("on", "true", "yes")
_OFF = ("off", "false", "no")


def think_levels(session: Session) -> tuple[str, ...]:
    """What /set think takes on the model in use, in the menu's order:
    "unset" always (no level: nothing sent), then the levels the provider says
    reach the model — every level where it cannot say, or has not
    listed the model yet, so an engine that reports nothing loses
    nothing. Both frontends' menus read this."""
    client = session._client()
    found = client.models.cached(session.model) if client is not None else None
    efforts = found.capabilities.reasoning if found and found.capabilities else None
    if efforts is None:
        return THINK_MENU
    return (THINK_UNSET, *(level for level in THINK_MENU if level in efforts))


def parameter_names(session: Session) -> tuple[str, ...]:
    """The /set parameters the provider in use reads, in PARAMETERS's
    order (`ProviderCapabilities.supported_params`) — every one while
    no model is selected, so a menu with nobody to ask still names
    them. Both frontends' menus read this. A list, never a gate: any
    known parameter may be set — one model is served by more than one
    provider, and they read different sets — and the wire sends the
    ones this provider reads."""
    client = session._client()
    if client is None:
        return tuple(PARAMETERS)
    supported = client.capabilities.supported_params
    return tuple(name for name in PARAMETERS if name in supported)


def set_think(session: Session, raw: str) -> str:
    """One of the wire's efforts, saved for the model in use, or "unset",
    which forgets it — the level follows the model, as its parameters do;
    "" reports where it stands and what the model
    takes (`think_levels`). Raises Refused for an unknown word, a level
    the model does not take, or no model — never for the provider: a
    level goes out on whatever knobs the provider reads, and on none
    where it reads none."""
    levels = think_levels(session)
    listed = ", ".join(levels)
    if not raw.strip():
        current = session.think if session.think else THINK_UNSET
        return f"Think: {current}. Levels for this model: {listed}."
    value = raw.strip().lower()
    if value != THINK_UNSET and value not in EFFORTS:
        raise Refused(f"Usage: /set think {'|'.join(levels)}")
    if session._client() is None:
        raise Refused(NO_MODEL_HINT)
    if value not in levels:
        raise Refused(f"{session.model} does not take {value}. Levels for this model: {listed}.")
    session._think = value
    if value == THINK_UNSET:
        return f"Think: unset{_save_model_settings(session)}"
    return f"Think: {value}{_save_model_settings(session)}"


def set_verbose(session: Session, raw: str) -> str:
    """Session-wide and persisted — verbose is a UI preference, never a
    per-model setting. "" reports where it stands."""
    value = raw.strip().lower()
    if value:
        if value in _ON:
            session._update_state(verbose=True)
        elif value in _OFF:
            session._update_state(verbose=False)
        else:
            raise Refused("Usage: /set verbose on|off")
    return f"Verbose: {'on' if session.verbose else 'off'}."


def set_autocorrect(session: Session, raw: str) -> str:
    """Session-wide and persisted, like verbose. Off means a name reaches
    the story exactly as it was typed, whoever the cast says that is."""
    value = raw.strip().lower()
    if value:
        if value in _ON:
            session._update_state(autocorrect=True)
        elif value in _OFF:
            session._update_state(autocorrect=False)
        else:
            raise Refused("Usage: /set autocorrect on|off")
    return f"Autocorrect: {'on' if session.autocorrect else 'off'}."


def set_notification(session: Session, raw: str) -> str:
    """Session-wide and persisted, like verbose. On means a reply landing
    calls you back to the screen — with what, and whether the terminal
    can, is the frontend's business."""
    value = raw.strip().lower()
    if value:
        if value in _ON:
            session._update_state(notification=True)
        elif value in _OFF:
            session._update_state(notification=False)
        else:
            raise Refused("Usage: /set notification on|off")
    return f"Notification: {'on' if session.notification else 'off'}."


def set_max_context(session: Session, raw: str) -> str:
    """Tokens the prompt may use at most: a number, 0 = the model's
    own max context; "" reports where it stands. The one /set that edits
    config.toml — [context] is the setting's single home — surgically,
    the pre-edit file backed up, the session updated in the same call;
    a write that could not land is SAID, not swallowed."""
    value = raw.strip().replace(",", "").replace("_", "")
    if value:
        try:
            tokens = int(value)
        except ValueError:
            raise Refused(
                "Usage: /set max_context <tokens> — 0 = the model's own max context"
            ) from None
        if tokens < 0:
            raise Refused("Max context cannot be negative — 0 means the model's own max context.")
        changed = tokens != session.max_context_setting
        session._config = replace(session._config, max_context=tokens)
        # fmt: off
        if changed and not surgery.update_config(
            session._paths.config_file,
            session._paths.config_backups_dir,
            [
                surgery.set_key("context", "max_context", row(
                    f"max_context = {tokens}",
                    "the prompt may use at most this many tokens; 0 = the model's own max context",
                ))
            ],
        ):
            return (
                f"Max context: {_stands(tokens)} — this session only, "
                f"config.toml could not be written."
            )
        # fmt: on
    return f"Max context: {_stands(session.max_context_setting)}."


def _stands(tokens: int) -> str:
    if tokens == 0:
        return "0 (the model's own max context)"
    return f"{tokens:,} tokens"


def set_parameter(session: Session, raw: str) -> str:
    """`<name> [value]` over PARAMETERS: set it ("reset" returns the
    model's own default; a bare name reports where it stands; "" lists
    what is set), auto-saved per model. Raises Refused for an unknown
    name, an unparsable value, or one outside the parameter's bounds
    (`Parameter`)."""
    tokens = raw.split()
    if not tokens:
        if not session.params:
            return "No parameters set."
        rows = "\n".join(f"  {name} = {value}." for name, value in session.params.items())
        return f"Parameters:\n{rows}"
    if session._client() is None:
        raise Refused(NO_MODEL_HINT)
    name = tokens[0]
    if name not in PARAMETERS:
        raise Refused(f"Unknown parameter {name!r}. Known: {', '.join(PARAMETERS)}.")
    value_raw = " ".join(tokens[1:])
    if not value_raw:
        # Asking is not setting: the bare name shows where it stands.
        if name in session.params:
            return f"{name} = {session.params[name]}"
        return f"Parameter {name} is at the model's own default."
    return set_parameter_value(session, name, value_raw)


def set_parameter_value(session: Session, name: str, value_raw: str) -> str:
    """One known parameter set to one value, auto-saved per model. The
    literal `reset` returns it to the model's own default, here and in
    the saved file. A surface with two fields (a name and a value) calls
    this; a typed line splits its own line first."""
    if session._client() is None:
        raise Refused(NO_MODEL_HINT)
    if name not in PARAMETERS:
        raise Refused(f"Unknown parameter {name!r}. Known: {', '.join(PARAMETERS)}.")
    if value_raw.strip().lower() == "reset":
        if name not in session.params:
            return f"Parameter {name} is already at its default."
        session._params.pop(name)
        return f"Parameter {name} reset to default{_save_model_settings(session)}"
    parameter = PARAMETERS[name]
    try:
        value = parameter.kind(value_raw)
    except ValueError:
        raise Refused(f"Could not parse {value_raw!r} as {parameter.kind.__name__}.") from None
    low, high = parameter.low, parameter.high
    if isinstance(value, int | float) and low is not None:
        below, above = value < low, high is not None and value > high
        if below or above:
            bounds = f"at least {low}" if high is None else f"between {low} and {high}"
            raise Refused(f"{name} must be {bounds}.")
    session._params[name] = value
    return f"{name} = {value}{_save_model_settings(session)}"


def _save_model_settings(session: Session) -> str:
    """Persist the model's entry — its parameters, and its thinking
    level when one is set (unset is the row's absence); the sentence
    tail says when the save did not land (the session still took the
    value)."""
    entry: dict[str, object] = dict(session.params)
    if session._think != THINK_UNSET:
        entry[THINK_KEY] = session._think
    try:
        models_file.save(session._paths.models_file, session.model, entry)
    except (OSError, ValueError) as e:
        return f" (this session only — could not save: {e})."
    return "."
