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

import json
from dataclasses import dataclass, replace

from otaku.backend.session import (
    EFFORT_LEVELS,
    NO_MODEL_HINT,
    PARAMETERS,
    SWITCH_LEVELS,
    THINK_MENU,
    Refused,
    Session,
)
from otaku.providers import reasoning
from otaku.settings import models as models_file
from otaku.settings import row
from otaku.settings.migrations import surgery
from otaku.settings.models import THINK_KEY, THINK_UNSET

_ON = ("on", "true", "yes")
_OFF = ("off", "false", "no")


@dataclass(frozen=True)
class ThinkChoices:
    """What /set think takes on the model in use: the `levels`, "unset"
    always first (no level: nothing sent), then the ladder's rungs the
    model grades, or off and on where it switches; and whether a
    `budget` — a number of tokens, 0 = off — is taken beside them."""

    levels: tuple[str, ...]
    budget: bool

    @property
    def listed(self) -> str:
        """The choices as one sentence's list."""
        tail = ", or a number of tokens" if self.budget else ""
        return ", ".join(self.levels) + tail

    @property
    def usage(self) -> str:
        return "Usage: /set think " + "|".join(self.levels) + ("|<tokens>" if self.budget else "")


def think_choices(session: Session) -> ThinkChoices:
    """The choices for the model in use, as the provider describes it
    (`ModelCapabilities`): every rung, and no budget, where it cannot
    say or has not listed the model yet — an engine that reports
    nothing loses nothing. Both frontends' menus read this."""
    client = session._client()
    found = client.models.cached(session.model) if client is not None else None
    caps = found.capabilities if found is not None else None
    if caps is None or (caps.reasoning_efforts is None and caps.reasoning_switch is None):
        return ThinkChoices(THINK_MENU, False)
    levels = [THINK_UNSET]
    if caps.reasoning_efforts:
        levels += [rung for rung in EFFORT_LEVELS if rung in caps.reasoning_efforts]
    elif caps.reasoning_switch:
        levels += SWITCH_LEVELS
    return ThinkChoices(tuple(levels), bool(caps.reasoning_budget))


def parameter_names(session: Session) -> tuple[str, ...]:
    """The /set parameters that reach the model in use, in PARAMETERS's
    order (`OpenAIClient.supported_params_of`: the wire's set, and the
    route's own within it where a catalog states one) — every one while
    no model is selected, so a menu with nobody to ask still names
    them. Both frontends' menus read this. A list, never a gate: any
    known parameter may be set — one model is served by more than one
    provider, and they read different sets — and the wire sends the
    ones that reach."""
    client = session._client()
    if client is None:
        return tuple(PARAMETERS)
    reaching = client.supported_params_of(session.model)
    return tuple(name for name in PARAMETERS if name in reaching)


def parameter_read(session: Session, name: str) -> bool:
    """Whether `name` reaches the model in use — the same answer the
    menu reads (`parameter_names`); True with nobody to ask."""
    client = session._client()
    return client is None or name in client.supported_params_of(session.model)


def parameter_bounds(session: Session, name: str) -> tuple[float | None, float | None]:
    """(low, high) the setter holds `name` to: the app's table
    (`PARAMETERS`, the catalogs' bounds), or the bounds the provider in
    use states for itself (`ProviderCapabilities.bounds` — a local
    engine takes any temperature). None on a side is no bound there.
    The page publishes these, so its mark and the refusal agree."""
    client = session._client()
    if client is not None and name in client.capabilities.bounds:
        return client.capabilities.bounds[name]
    parameter = PARAMETERS[name]
    return parameter.low, parameter.high


def parameter_text(value: object) -> str:
    """A parameter's value as a reader sees and types it: a number as
    it is, the stop strings as `parse_stops` reads them back — each a
    JSON string, so a newline shows as \\n — one bare where it holds
    no quote or space."""
    if isinstance(value, list):
        return " ".join(json.dumps(stop, ensure_ascii=False) for stop in value)
    return str(value)


def parse_stops(raw: str) -> list[str]:
    """Stop strings as typed: JSON strings separated by spaces —
    `"\\nUser:" "END"` — so a stop may hold a newline or a space; a
    text that does not open with a quote is one stop, as it is. Raises
    ValueError for a quote left open, or an empty stop."""
    text = raw.strip()
    if not text.startswith('"'):
        stops = [text] if text else []
    else:
        decoder = json.JSONDecoder()
        stops = []
        at = 0
        while at < len(text):
            if text[at].isspace():
                at += 1
                continue
            if text[at] != '"':
                raise ValueError(f"a stop string needs quotes at {text[at:]!r}")
            stop, at = decoder.raw_decode(text, at)
            stops.append(stop)
    if not stops or any(not stop for stop in stops):
        raise ValueError("an empty stop")
    return stops


def set_think(session: Session, raw: str) -> str:
    """A thinking level — a rung, off or on, or a budget in tokens —
    saved for the model in use, or "unset", which forgets it: the level
    follows the model, as its parameters do. "" reports where it stands
    and what the model takes (`think_choices`). Raises Refused for a
    word outside the vocabulary, a level the model does not take, or no
    model — never for the provider: a level goes out on whatever knobs
    the provider reads, and on none where it reads none."""
    choices = think_choices(session)
    if not raw.strip():
        current = session.think if session.think else THINK_UNSET
        return f"Think: {current}. Levels for this model: {choices.listed}."
    value = raw.strip().lower()
    if value != THINK_UNSET and not reasoning.is_level(value):
        raise Refused(choices.usage)
    if session._client() is None:
        raise Refused(NO_MODEL_HINT)
    budget = reasoning.budget_of(value)
    if budget is not None:
        if not choices.budget:
            raise Refused(
                f"{session.model} does not take a thinking budget. "
                f"Levels for this model: {choices.listed}."
            )
        value = str(budget)  # as a number reads: no leading zeros
    elif value not in choices.levels:
        raise Refused(
            f"{session.model} does not take {value}. Levels for this model: {choices.listed}."
        )
    session._think = value
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
    # The name, then the value as typed — whole, since a stop string
    # may hold spaces.
    name, _, value_raw = raw.strip().partition(" ")
    if not name:
        if not session.params:
            return "No parameters set."
        rows = "\n".join(
            f"  {name} = {parameter_text(value)}." for name, value in session.params.items()
        )
        return f"Parameters:\n{rows}"
    if session._client() is None:
        raise Refused(NO_MODEL_HINT)
    if name not in PARAMETERS:
        raise Refused(f"Unknown parameter {name!r}. Known: {', '.join(PARAMETERS)}.")
    if not value_raw.strip():
        # Asking is not setting: the bare name shows where it stands.
        if name in session.params:
            return f"{name} = {parameter_text(session.params[name])}"
        return f"Parameter {name} is not set: the engine's own default applies."
    return set_parameter_value(session, name, value_raw)


def set_parameter_value(session: Session, name: str, value_raw: str) -> str:
    """One known parameter set to one value, auto-saved per model. The
    literal `reset` unsets it, here and in the saved file, and the
    engine's own default applies again. A surface with two fields (a
    name and a value) calls this; a typed line splits its own line
    first. The value is held to `parameter_bounds`. A parameter the
    provider in use does not read is set all the same — one model is
    served by more than one provider — and the answer says so."""
    if session._client() is None:
        raise Refused(NO_MODEL_HINT)
    if name not in PARAMETERS:
        raise Refused(f"Unknown parameter {name!r}. Known: {', '.join(PARAMETERS)}.")
    if value_raw.strip().lower() == "reset":
        if name not in session.params:
            return f"Parameter {name} is not set."
        session._params.pop(name)
        saved = _save_model_settings(session)
        return f"Parameter {name} unset: the engine's own default applies{saved}"
    parameter = PARAMETERS[name]
    value: object
    try:
        value = parse_stops(value_raw) if parameter.kind is list else parameter.kind(value_raw)
    except ValueError:
        raise Refused(f"Could not parse {value_raw!r} as {parameter.kind.__name__}.") from None
    low, high = parameter_bounds(session, name)
    if isinstance(value, int | float) and (low is not None or high is not None):
        below = low is not None and value < low
        above = high is not None and value > high
        if below or above:
            if high is None:
                bounds = f"at least {low}"
            elif low is None:
                bounds = f"at most {high}"
            else:
                bounds = f"between {low} and {high}"
            raise Refused(f"{name} must be {bounds}.")
    session._params[name] = value
    said = f"{name} = {parameter_text(value)}{_save_model_settings(session)}"
    if not parameter_read(session, name):
        said += (
            f" Not read by {session.provider}: kept for the model, sent where a provider reads it."
        )
    return said


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
