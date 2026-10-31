"""The installation's settings as they stand: state.toml's (the story
last landed in, the /set switches), config.toml's (the context limit,
how long the prompt stands idle, the two frontends' slices) and the
settings table's shared reminder — read fresh, written over the file as
it stands, a write that cannot land refused (`Unavailable`), the last
story alone best effort. And the web's slice in force, the command line
and the environment over the file (`get_web_settings`), with the sign-in's
check against its password.

The context limit LIVES in config.toml's [context], as `max_context`,
beside head_messages and min_tail_messages (docs/context_design.md's
home for it), so it is edited there surgically — the picker's
provider-field saves are the precedent — rather than shadowed from a
second file.
"""

import functools
import os
from dataclasses import dataclass, replace
from pathlib import Path

from otaku.backend.app import passwords
from otaku.backend.app.core.services import App
from otaku.backend.errors import Conflict, Invalid, Unavailable
from otaku.formatting import toml_scalar
from otaku.settings import row
from otaku.settings.config import TerminalSettings, WebSettings
from otaku.settings.migrations import surgery

# The web's host, port, sign-in password and https (on/off), and whether
# the terminal serves the web beside it (on/off), from the environment,
# over config.toml's.
_TERMINAL_SERVE_VAR = "OTAKU_TERMINAL_SERVE"
_WEB_HOST_VAR = "OTAKU_WEB_HOST"
_WEB_PORT_VAR = "OTAKU_WEB_PORT"
_WEB_PASSWORD_VAR = "OTAKU_WEB_PASSWORD"
_WEB_HTTPS_VAR = "OTAKU_WEB_HTTPS"


@dataclass(frozen=True)
class AppSettings:
    """The app's settings as they stand: the story last landed in — by
    either frontend, and the one a launch resumes (None where it is
    gone) — the /set switches, the context limit (the most tokens a
    request may use; 0 = none, the model's own window) and how long the
    prompt stands idle before the lore pass runs on its own. The model in
    use and the extraction pin are the app's (`app.core.services`)."""

    last_story: int | None
    verbose: bool
    autocorrect: bool
    notification: bool
    context_limit: int
    idle_seconds: float


def get_settings(app: App) -> AppSettings:
    """The settings as their files stand now — a story deleted since
    reads as none."""
    state = app._state_config()
    config = app._config()
    story = state.story
    return AppSettings(
        last_story=story if story and app._store.stories.exists(story) else None,
        verbose=state.verbose,
        autocorrect=state.autocorrect,
        notification=state.notification,
        context_limit=config.context_limit,
        idle_seconds=config.idle_seconds,
    )


def get_terminal_settings(app: App) -> TerminalSettings:
    """The terminal frontend's slice of config.toml: its looks, and
    whether it serves the web beside it — `OTAKU_TERMINAL_SERVE` (on/off)
    over the file's, read with every call."""
    terminal = app._config().terminal
    env_serve = os.environ.get(_TERMINAL_SERVE_VAR, "").strip().lower()
    if env_serve in ("on", "off"):
        terminal = replace(terminal, serve=env_serve == "on")
    return terminal


def get_web_settings(app: App) -> tuple[WebSettings, Path, Path]:
    """The web frontend's slice in force, and the two directories it
    serves from: the reader's own (custom.css, fonts/) and the TLS pair's.
    Each setting is the environment's where a variable sets it — read
    with every call, so one set while otaku runs is the next server's —
    and config.toml's otherwise. A variable that holds no value — a port
    out of range, https neither on nor off — is passed over, and the
    launch says so (`unusable_variables`)."""
    web = app._config().web
    env_host = os.environ.get(_WEB_HOST_VAR, "").strip()
    if env_host:
        web = replace(web, host=env_host)
    env_port = os.environ.get(_WEB_PORT_VAR, "").strip()
    if env_port.isdigit() and 1 <= int(env_port) <= 65535:
        web = replace(web, port=int(env_port))
    env_password = os.environ.get(_WEB_PASSWORD_VAR, "")
    if env_password:
        web = replace(web, password=_hashed(env_password))
    env_https = os.environ.get(_WEB_HTTPS_VAR, "").strip().lower()
    if env_https in ("on", "off"):
        web = replace(web, https=env_https == "on")
    return web, app._paths.custom_web_dir, app._paths.cert_dir


def unusable_variables() -> list[str]:
    """A sentence for each settings variable set to what it cannot hold,
    the file's value used in its place — for the launch to say."""
    said = []
    port = os.environ.get(_WEB_PORT_VAR, "").strip()
    if port and not (port.isdigit() and 1 <= int(port) <= 65535):
        said.append(f"{_WEB_PORT_VAR}={port!r} is not a port; config.toml's is used.")
    for name in (_WEB_HTTPS_VAR, _TERMINAL_SERVE_VAR):
        value = os.environ.get(name, "").strip()
        if value and value.lower() not in ("on", "off"):
            said.append(f"{name}={value!r} is neither on nor off; config.toml's is used.")
    return said


@functools.cache
def _hashed(password: str) -> str:
    """The environment's password hashed once per run: hashing is slow on
    purpose and `get_web_settings` runs on every read of the slice, and
    one hash keeps sign-ins valid across `/set serve off` and `on`."""
    return passwords.hash(password)


def check_password(app: App, password: str) -> None:
    """Check `password` against the web sign-in's, whose hash the slice
    in force holds (`get_web_settings`) — slow on purpose. Safe from any
    thread: it reads the config alone. Raises Conflict where no password
    is set, Invalid where it is not the one."""
    password_hash = get_web_settings(app)[0].password
    if not password_hash:
        raise Conflict("This otaku asks for no password.")
    if not passwords.check(password, password_hash):
        raise Invalid("Check the password and try again.")


def set_last_story(app: App, story_id: int | None) -> str:
    """The story a frontend landed in — the one a launch resumes; None
    forgets it — and the sentence. Best effort: a file that cannot take
    it changes nothing."""
    app._update_state_config(best_effort=True, story=story_id or 0)
    return f"A launch resumes story {story_id}." if story_id else "A launch resumes no story."


def set_verbose(app: App, on: bool) -> str:
    """Verbose on or off — installation-wide, a frontend's setting and
    never a model's."""
    app._update_state_config(verbose=on)
    return f"Verbose: {_word(on)}."


def set_autocorrect(app: App, on: bool) -> str:
    """Autocorrect on or off. Off means a name reaches the story exactly
    as it was typed, whoever the cast says that is."""
    app._update_state_config(autocorrect=on)
    return f"Autocorrect: {_word(on)}."


def set_notification(app: App, on: bool) -> str:
    """Notification on or off. On means a reply landing calls the reader
    back to the screen — with what, and whether it can, is the
    frontend's business."""
    app._update_state_config(notification=on)
    return f"Notification: {_word(on)}."


def set_context_limit(app: App, tokens: int) -> str:
    """The most tokens a prompt may use, 0 = the model's own window —
    written into config.toml's [context], the setting's one home,
    surgically, the pre-edit file backed up. Raises Invalid for a
    negative count, Unavailable where the file could not be written:
    nothing changed."""
    if tokens < 0:
        raise Invalid("Max context cannot be negative — 0 means the model's own max context.")
    # fmt: off
    if tokens != app._config().context_limit and not surgery.update_config(
        app._paths.config_file,
        app._paths.config_backups_dir,
        [
            surgery.set_key("context", "max_context", row(
                f"max_context = {tokens}",
                "the prompt may use at most this many tokens; 0 = the model's own max context",
            ))
        ],
    ):
        raise Unavailable("Not saved — config.toml could not be written.")
    # fmt: on
    return f"Max context: {context_limit_text(tokens)}."


def set_show_thinking(app: App, on: bool) -> str:
    """Whether the chat prints a reply's thinking as it streams, or only
    how long it took — written into config.toml's [terminal] as
    `set_serve` writes. Raises Unavailable where the file could
    not be written: nothing changed."""
    # fmt: off
    if on != app._config().show_thinking and not surgery.update_config(
        app._paths.config_file,
        app._paths.config_backups_dir,
        [
            surgery.set_key("terminal", "show_thinking", row(
                f"show_thinking = {toml_scalar(on)}",
                "print the model's thinking as it streams (/set show_thinking)",
            ))
        ],
    ):
        raise Unavailable("Not saved — config.toml could not be written.")
    # fmt: on
    return f"Show thinking: {_word(on)}."


def set_serve(app: App, on: bool) -> str:
    """Whether the chat serves the web interface beside it — written into
    config.toml's [terminal] as `serve`, surgically, the pre-edit file
    backed up; the serving itself is the frontend's. Raises Unavailable
    where the file could not be written: nothing changed."""
    # fmt: off
    if on != app._config().serve and not surgery.update_config(
        app._paths.config_file,
        app._paths.config_backups_dir,
        [
            surgery.set_key("terminal", "serve", row(
                f"serve = {toml_scalar(on)}",
                "the terminal UI also serves the web interface while it runs (/set serve)",
            ))
        ],
    ):
        raise Unavailable("Not saved — config.toml could not be written.")
    # fmt: on
    return f"Serve: {_word(on)}."


def context_limit_text(tokens: int) -> str:
    """A context limit as a sentence states it — the write's, and the
    settings' own reading of the limit in force."""
    if tokens == 0:
        return "0 (the model's own max context)"
    return f"{tokens:,} tokens"


def _word(on: bool) -> str:
    return "on" if on else "off"
