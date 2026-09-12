"""The session's knobs: the /set family.

Values persist where they belong — state.toml for session-wide toggles,
models.toml per model, and never in the user-owned config, with ONE
exception: `max_context` LIVES in config.toml's [context] beside
head_messages and min_tail_messages (docs/context_design.md's home for it),
so /set max_context edits that file surgically — the picker's
provider-field saves are the precedent — rather than shadowing it from
a second file. Every operation takes the raw argument text and parses
it itself; every one returns the confirmation to show and raises
Refused for what it declines.
"""

from dataclasses import replace

from otaku.backend.session import KNOWN_PARAMS, NO_MODEL_HINT, Refused, Session
from otaku.settings import models as models_file
from otaku.settings import row
from otaku.settings.migrations import surgery
from otaku.settings.state import THINK_DEFAULT, THINK_LEVELS

_ON = ("on", "true", "yes")
_OFF = ("off", "false", "no")
# The typed sugar over the stored levels — a command-surface convenience,
# where THINK_LEVELS is what state.toml may hold.
THINK_ALIASES = {"on": "medium", "off": "none"}


def set_think(session: Session, raw: str) -> str:
    """A THINK_LEVELS value, an alias (on/off), or "default" (send
    nothing); "" reports where it stands. Raises Refused for an unknown
    level or no model — never for the provider: a level goes out on
    whatever knobs the provider reads, and on none where it reads none."""
    if not raw.strip():
        return f"Think: {session.think if session.think else 'default'}."
    value = THINK_ALIASES.get(raw.strip().lower(), raw.strip().lower())
    if value == THINK_DEFAULT:
        session._update_state(think=THINK_DEFAULT)
        return "Think: default (nothing sent — the model decides)."
    if value not in THINK_LEVELS:
        raise Refused("Usage: /set think on|off|none|low|medium|high|max|default")
    if session._client() is None:
        raise Refused(NO_MODEL_HINT)
    session._update_state(think=value)
    return f"Think: {value}."


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
    """`<name> [value]` over KNOWN_PARAMS: set it ("reset" returns the
    model's own default; a bare name reports where it stands; "" lists
    what is set), auto-saved per model. Raises Refused for an unknown
    name or an unparsable value."""
    tokens = raw.split()
    if not tokens:
        if not session.params:
            return "No parameters set."
        rows = "\n".join(f"  {name} = {value}." for name, value in session.params.items())
        return f"Parameters:\n{rows}"
    if session._client() is None:
        raise Refused(NO_MODEL_HINT)
    name = tokens[0]
    if name not in KNOWN_PARAMS:
        raise Refused(f"Unknown parameter {name!r}. Known: {', '.join(KNOWN_PARAMS)}.")
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
    if name not in KNOWN_PARAMS:
        raise Refused(f"Unknown parameter {name!r}. Known: {', '.join(KNOWN_PARAMS)}.")
    if value_raw.strip().lower() == "reset":
        if name not in session.params:
            return f"Parameter {name} is already at its default."
        session._params.pop(name)
        return f"Parameter {name} reset to default{_save_params(session)}"
    coerce = KNOWN_PARAMS[name]
    try:
        value = coerce(value_raw)
    except ValueError:
        raise Refused(f"Could not parse {value_raw!r} as {coerce.__name__}.") from None
    session._params[name] = value
    return f"{name} = {value}{_save_params(session)}"


def _save_params(session: Session) -> str:
    """Persist the model's parameters; the sentence tail says when the
    save did not land (the session still took the value)."""
    try:
        models_file.save_parameters(session._paths.models_file, session.model, dict(session.params))
    except (OSError, ValueError) as e:
        return f" (this session only — could not save: {e})."
    return "."
