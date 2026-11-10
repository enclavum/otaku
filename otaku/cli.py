"""The process entry: argv routing and nothing else. Bare `otaku` opens
the terminal over the app and `otaku serve` opens the page over
it — each frontend's own `run` is its whole life, and this file only
opens the app for it and answers for what escapes. `otaku update`
self-updates (`update.py` is its machinery); `otaku logs …` pages the
day-rotated logs (the sealed request bodies unlocked through
`backend.launch`). otaku's home folder is resolved HERE (the env vars are
entry-point business) and passed down as a plain path.
"""

import getpass
import io
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

import click

from otaku import __version__, envvars, logging
from otaku import update as updater
from otaku import web as web_frontend
from otaku.backend import launch as backend_launch
from otaku.backend.launch import ConfigError, DatabaseError, EncryptionError
from otaku.formatting import pretty_path
from otaku.terminal import run as terminal_frontend

_L = TypeVar("_L", bound=logging.DailyLog)


class _DeclaredOrderGroup(click.Group):
    """Subcommands listed in declaration order, not alphabetically."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)


class _NameConfigFile(click.Command):
    """A command whose help names config.toml where it actually is — its
    `{config_file}` filled in when the help is shown."""

    def format_help_text(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        config = pretty_path(backend_launch.config_path(resolve_root()))
        self.help = (self.help or "").replace("{config_file}", config)
        super().format_help_text(ctx, formatter)


@click.group(
    cls=_DeclaredOrderGroup,
    invoke_without_command=True,
    # Wide help: every command's description prints in full, on one line,
    # instead of click's wrapped-and-truncated defaults.
    context_settings={"help_option_names": ["-h", "--help"], "max_content_width": 160},
)
@click.version_option(
    __version__, "-v", "--version", prog_name="otaku", message="%(prog)s %(version)s"
)
@click.pass_context
def main(ctx: click.Context) -> None:
    """A roleplay app."""
    if sys.platform == "win32":
        # A pipe or a file there takes the ANSI codepage, which has no `→`
        # or `…`; a console is UTF-8 already. Python 3.15 does this itself.
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, io.TextIOWrapper):
                stream.reconfigure(encoding="utf-8")
    if ctx.invoked_subcommand is not None:
        return
    root = resolve_root()
    try:
        app = backend_launch.open_app(root, ask_secret=getpass.getpass)
    except ConfigError as e:
        click.echo(f"Config error: {e}", err=True)
        ctx.exit(2)
    except (EncryptionError, DatabaseError) as e:
        click.echo(str(e), err=True)
        ctx.exit(1)
    try:
        app.check_for_update(updater.fetch_latest_version)
        terminal_frontend.run(app)
    except Exception as e:
        _crashed(root, e)
        ctx.exit(1)
    finally:
        app.close()


@main.command(cls=_NameConfigFile, short_help="Serve the web interface")
@click.option(
    "--any-host",
    "is_any_host",
    is_flag=True,
    help="Make otaku reachable from other devices (same as --host 0.0.0.0).",
)
@click.option("--host", metavar="HOST", help="Override host from settings.")
@click.option(
    "--port",
    metavar="PORT",
    type=click.IntRange(1, 65535),
    help="Override port from settings.",
)
def serve(host: str | None, is_any_host: bool, port: int | None) -> None:
    """Serve otaku's web interface; settings are configured in
    {config_file}."""
    ctx = click.get_current_context()
    if is_any_host:
        if host is not None:
            raise click.UsageError("--any-host and --host cannot be used together.")
        host = "0.0.0.0"
    root = resolve_root()
    # Typed for this run: over the environment's and config.toml's.
    if host is not None:
        os.environ[envvars.WEB_HOST] = host
    if port is not None:
        os.environ[envvars.WEB_PORT] = str(port)
    try:
        app = backend_launch.open_app(root, ask_secret=getpass.getpass)
    except ConfigError as e:
        click.echo(f"Config error: {e}", err=True)
        ctx.exit(2)
    except (EncryptionError, DatabaseError) as e:
        click.echo(str(e), err=True)
        ctx.exit(1)
    restart = False
    try:
        app.check_for_update(updater.fetch_latest_version)
        restart = web_frontend.run(app)
    except web_frontend.ServeError as e:
        # An address that cannot be listened on: another otaku already
        # has it, the host does not name this machine, the port is the
        # system's. All of them are one line to fix in config.toml.
        click.echo(f"otaku: {e}", err=True)
        ctx.exit(1)
    except Exception as e:
        _crashed(root, e)
        ctx.exit(1)
    finally:
        app.close()
    if restart:
        # Ctrl+R: served again on fresh sources. The process is REPLACED
        # rather than a child spawned — the PID, the terminal and the
        # shell's job stay — over the same command line, and the app
        # closed above leaves the database clean for the next.
        os.execv(sys.executable, [sys.executable, "-m", "otaku", *sys.argv[1:]])


# The command's name before 0.9.0, kept so a script or a habit still works;
# hidden from --help.
main.add_command(
    _NameConfigFile(
        "web", callback=serve.callback, params=serve.params, help=serve.help, hidden=True
    )
)


@main.group(
    cls=_DeclaredOrderGroup,
    short_help="Rotated logs: requests, system and error",
)
def logs() -> None:
    """Rotated logs: `requests` (what the models were sent), `system`
    (the background worker's own account), and `error` (crash
    tracebacks)."""


@logs.command("requests", short_help="Show the model-request log")
@click.argument("day", required=False)
@click.option("--list", "list_days", is_flag=True, help="List the available log days.")
def logs_requests(day: str | None, list_days: bool) -> None:
    """Print one day's model-request log (DAY as YYYY-MM-DD, default
    today) — the sealed bodies unlocked the way the app unlocks."""
    ctx = click.get_current_context()
    root = resolve_root()
    try:
        request_log = backend_launch.request_log(root, ask_secret=getpass.getpass)
    except ConfigError as e:
        click.echo(f"Config error: {e}", err=True)
        ctx.exit(2)
    except EncryptionError as e:
        click.echo(str(e), err=True)
        ctx.exit(1)
    _page(request_log, day, list_days, "request", logging.render_requests)


@logs.command("system", short_help="Show the background work")
@click.argument("day", required=False)
@click.option("--list", "list_days", is_flag=True, help="List the available log days.")
def logs_system(day: str | None, list_days: bool) -> None:
    """Print one day's system log — the background worker's account of
    itself (DAY as YYYY-MM-DD, default today)."""
    _page(backend_launch.system_log(resolve_root()), day, list_days, "system", logging.render_plain)


@logs.command("error", short_help="Show crash tracebacks")
@click.argument("day", required=False)
@click.option("--list", "list_days", is_flag=True, help="List the available log days.")
def logs_error(day: str | None, list_days: bool) -> None:
    """Print one day's error log — its crash tracebacks (DAY as
    YYYY-MM-DD, default today)."""
    _page(backend_launch.error_log(resolve_root()), day, list_days, "error", logging.render_plain)


@main.command(short_help="Update otaku to the latest release")
def update() -> None:
    """Update otaku in place, using the tool that it was installed with.
    The new version runs at the next launch."""
    command = updater.upgrade_command()
    if command is None:
        click.echo("This otaku runs from a source checkout — update it with git:")
        click.echo("  git pull")
        return
    click.echo("Updating via: " + " ".join(command))
    code = updater.run(command)
    root = resolve_root()
    if code == 0:
        backend_launch.system_log(root).record(f"app update finished ({' '.join(command)})")
        click.echo("Done — the new version runs at the next otaku.")
        return
    backend_launch.system_log(root).record(f"app update failed ({' '.join(command)}, exit {code})")
    click.echo("The update did not finish — run the one matching your install:", err=True)
    for manual in updater.MANUAL_COMMANDS:
        click.echo(f"  {manual}", err=True)
    click.get_current_context().exit(1)


@main.command(short_help="Uninstall otaku and, optionally, all its data")
def uninstall() -> None:
    """Uninstall otaku, using the tool that it was installed with, and,
    optionally, all its data."""
    ctx = click.get_current_context()
    if not sys.stdin.isatty():
        click.echo(
            "otaku uninstall asks before it deletes anything: run it in a terminal.", err=True
        )
        ctx.exit(1)
    command = updater.uninstall_command()
    if command is None:
        # A checkout's data is a home folder like any other's: the purge stands.
        click.echo("otaku runs from a git checkout — remove the checked-out repo manually.")
    elif not click.confirm("Uninstall otaku?", default=True, prompt_suffix=" "):
        return
    purging = False
    if click.confirm("Purge all data?", default=False, prompt_suffix=" "):
        click.echo(
            "WARNING: purging will delete all your stories, characters library, uploaded "
            "pictures, backups and otaku's keys in your keychain (if any)."
        )
        purging = click.confirm("Are you sure you want to purge?", default=False, prompt_suffix=" ")
    if purging:
        purged = pretty_path(backend_launch.purge(resolve_root()))
        click.echo(f"Purged {purged} and otaku's keys in your keychain (if any).")
    if command is None:
        return
    if sys.platform == "win32":
        # A running otaku.exe cannot remove itself.
        click.echo("Run this once otaku has exited: " + " ".join(command))
        return
    if updater.run(command) != 0:
        click.echo("The uninstall did not finish — run it yourself: " + " ".join(command), err=True)
        ctx.exit(1)
    click.echo("Uninstalled otaku.")


def resolve_root() -> Path | None:
    """otaku's home folder as the first of `envvars.HOME` set names it —
    None for the default (backend owns the default location; the env vars
    are the entry's)."""
    for name in envvars.HOME:
        raw = os.environ.get(name, "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def _crashed(root: Path | None, e: Exception) -> None:
    """The last resort, wherever a frontend was running: whatever
    escaped every inner containment. The story is safe — every store
    write is transactional — so say so, record the traceback, and leave
    quietly."""
    path = backend_launch.error_log(root).record("unhandled", e)
    click.echo(
        f"otaku crashed — your story is safe in the database. The crash is "
        f"recorded in {pretty_path(path)}; please attach it to an issue.",
        err=True,
    )


def _page(
    log: _L,
    day: str | None,
    list_days: bool,
    name: str,
    render: Callable[[_L, str], str | Iterable[str]],
) -> None:
    """Every `logs` subcommand's body once its log is in hand: list the
    log's days, or page one day through the log's own renderer — the
    rendering itself is `logging`'s; only the echoing, the paging and
    the exit codes live here."""
    ctx = click.get_current_context()
    if list_days:
        days = log.get_days()
        if not days:
            click.echo(f"no {name} logs yet")
        for row in logging.day_rows(days):
            click.echo(row)
        return
    stamp = logging.resolve_day(day)
    if stamp is None:
        click.echo(
            "DAY must be YYYY-MM-DD (or YYYYMMDD), e.g. otaku logs requests 2026-07-25", err=True
        )
        ctx.exit(2)
    if not log.get_path(stamp).exists():
        click.echo(f"no {name} log for {logging.dashed(stamp)}", err=True)
        ctx.exit(1)
    click.echo_via_pager(render(log, stamp))
