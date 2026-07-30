"""Model and settings commands: /model and /set.

`/set think` and `/set verbose` persist to state.toml (session-wide);
`/set parameter` auto-saves per model to models.toml — settings follow the
thing they describe, and nothing is written into the user-owned config.
"""

from otaku.chat.session import (
    KNOWN_PARAMS,
    NO_MODEL_HINT,
    THINK_ALIASES,
    THINK_LEVELS,
    Session,
)
from otaku.settings import models as models_file
from otaku.store import Store


def cmd_model(session: Session, store: Store, args: list[str]) -> None:
    """Switch the model for the rest of this session, keeping the current
    context (handy for comparing models on one prompt — switch, then
    /regen). `/model` opens the picker; `/model PROVIDER/MODEL` switches
    directly. A successful switch is remembered as the last-used model."""
    providers = session.config.providers
    if args:
        head, _, rest = " ".join(args).partition("/")
        if head in providers and rest:
            _switch_model(session, head, rest)
        else:
            known = ", ".join(sorted(providers))
            print(f"Use PROVIDER/MODEL (providers: {known}), or /model with no args to pick.")
        return
    if session.tui.pick_model is None:
        print("No picker available; use /model PROVIDER/MODEL.")
        return
    spec = session.tui.pick_model(session.full_model_name)
    if not spec:
        return  # cancelled, or nothing to pick — the picker said why
    head, _, rest = spec.partition("/")
    _switch_model(session, head, rest)


def cmd_set(session: Session, store: Store, args: list[str]) -> None:
    if not args:
        print("Usage: /set think <level> | /set verbose on|off | /set parameter <name> [value]")
        return
    sub, *rest = args
    if sub == "think":
        _set_think(session, rest)
    elif sub == "verbose":
        _set_verbose(session, rest)
    elif sub == "parameter":
        _set_parameter(session, rest)
    else:
        print(f"Unknown subcommand: /set {sub}")


def _switch_model(session: Session, provider_name: str, model: str) -> None:
    if provider_name not in session.config.providers:
        print(f"Unknown provider {provider_name!r}.")
        return
    if f"{provider_name}/{model}" == session.full_model_name:
        print(f"Already using {session.full_model_name}.")
        return
    session.provider = session.config.providers[provider_name]
    session.model = model
    session.reload_params()
    session.save_state()
    print(f"Switched to {session.full_model_name}.")


def _set_think(session: Session, rest: list[str]) -> None:
    if not rest:
        print(f"Think: {session.think if session.think else 'default'}.")
        return
    value = THINK_ALIASES.get(rest[0].lower(), rest[0].lower())
    if value == "default":
        session.think = None
        session.save_state()
        print("Think: default (nothing sent — the model decides). (saved)")
        return
    if value not in THINK_LEVELS:
        print("Usage: /set think on|off|none|low|medium|high|max|default")
        return
    if session.provider is None:
        print(NO_MODEL_HINT)
        return
    if value != "none" and not session.provider.supports_thinking:
        print(f"Thinking is not supported by provider {session.provider.name!r}.")
        return
    session.think = value
    session.save_state()
    print(f"Think: {value}. (saved)")


def _set_verbose(session: Session, rest: list[str]) -> None:
    """Session-wide and persisted — verbose is a UI preference, never a
    per-model setting."""
    if not rest:
        print(f"Verbose: {'on' if session.verbose else 'off'}.")
        return
    value = rest[0].lower()
    if value in ("on", "true", "yes"):
        session.verbose = True
    elif value in ("off", "false", "no"):
        session.verbose = False
    else:
        print("Usage: /set verbose on|off")
        return
    session.save_state()
    print(f"Verbose: {'on' if session.verbose else 'off'}. (saved)")


def _set_parameter(session: Session, rest: list[str]) -> None:
    if not rest:
        if not session.params:
            print("No parameters set.")
            return
        print("Parameters:")
        for name, value in session.params.items():
            print(f"  {name} = {value}.")
        return
    if session.provider is None:
        print(NO_MODEL_HINT)
        return
    name = rest[0]
    if name not in KNOWN_PARAMS:
        print(f"Unknown parameter {name!r}. Known: {', '.join(KNOWN_PARAMS)}.")
        return
    raw = " ".join(rest[1:])
    if not raw:
        # Asking is not setting: the bare name shows where it stands.
        if name in session.params:
            print(f"{name} = {session.params[name]}")
        else:
            print(f"Parameter {name} is at the model's own default.")
        return
    # The literal `reset` returns the parameter to the model's own
    # default — here and in the saved file.
    if raw.lower() == "reset":
        if name in session.params:
            session.params.pop(name)
            print(f"Parameter {name} reset to default{_save_params(session)}")
        else:
            print(f"Parameter {name} is already at its default.")
        return
    coerce = KNOWN_PARAMS[name]
    try:
        value = coerce(raw)
    except ValueError:
        print(f"Could not parse {raw!r} as {coerce.__name__}.")
        return
    session.params[name] = value
    print(f"{name} = {value}{_save_params(session)}")


def _save_params(session: Session) -> str:
    try:
        models_file.save_parameters(session.paths, session.model, dict(session.params))
    except (OSError, ValueError) as e:
        return f" (this session only — could not save: {e})."
    return f" (saved for {session.model})."
