"""otaku command-line entry point.

`otaku` resumes the model and story the last session left off on and opens
the chat; on a first run — or when the remembered model's provider is gone
— it opens the model picker instead. `otaku update` updates the app in
place; `otaku logs requests` prints a day's model-request log, `otaku logs
system` the lore worker's own account.
"""

from datetime import datetime

import click

from otaku import __version__, crypto
from otaku import app as app_mod
from otaku import update as updater
from otaku.formatting import pretty_path
from otaku.logs import view as logs_view
from otaku.logs.errors import ErrorLog
from otaku.logs.requests import RequestLog
from otaku.logs.system import SystemLog
from otaku.paths import Paths
from otaku.settings import config as config_mod
from otaku.store import DatabaseError


@click.group(
    invoke_without_command=True,
    # Wide help: every command's description prints in full, on one line,
    # instead of click's wrapped-and-truncated defaults.
    context_settings={"help_option_names": ["-h", "--help"], "max_content_width": 160},
)
@click.version_option(__version__, "-v", "--version", prog_name="otaku")
@click.pass_context
def main(ctx: click.Context) -> None:
    """A roleplay terminal client."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        application = app_mod.App()
    except config_mod.ConfigError as e:
        click.echo(f"config error: {e}", err=True)
        ctx.exit(2)
    except (crypto.CryptoError, DatabaseError) as e:
        click.echo(str(e), err=True)
        ctx.exit(1)
    try:
        application.run()
    except Exception as e:
        # The last resort: whatever escaped every inner containment. The
        # story is safe — every store write is transactional — so say so,
        # record the traceback, and leave quietly.
        path = ErrorLog(Paths.resolve()).record("unhandled", e)
        click.echo(
            f"otaku crashed — your story is safe in the database. The crash is "
            f"recorded in {pretty_path(path)}; please attach it to an issue.",
            err=True,
        )
        ctx.exit(1)
    finally:
        application.close()


@main.command(short_help="Update otaku to the latest release")
def update() -> None:
    """Update otaku in place, whatever installed it: a Homebrew or uv
    install runs its own upgrade, a source checkout is left to git, and
    anything else gets pip. The new version runs at the next launch."""
    command = updater.upgrade_command()
    if command is None:
        click.echo("This otaku runs from a source checkout — update it with git:")
        click.echo("  git pull")
        return
    click.echo("Updating via: " + " ".join(command))
    if updater.run(command) == 0:
        click.echo("Done — the new version runs at the next otaku.")
        return
    click.echo("The update did not finish — run the one matching your install:", err=True)
    for manual in updater.MANUAL_COMMANDS:
        click.echo(f"  {manual}", err=True)
    click.get_current_context().exit(1)


class _DeclaredOrderGroup(click.Group):
    """Subcommands listed in declaration order, not alphabetically."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)


@main.group(
    cls=_DeclaredOrderGroup,
    short_help="Day-rotated logs: requests (what the models were sent), "
    "system (the lore worker's account), error (contained crashes)",
)
def logs() -> None:
    """Day-rotated logs: `requests` (what the models were sent), `system`
    (the lore worker's own account), and `error` (every contained crash's
    traceback)."""


@logs.command(
    "requests",
    short_help="Show the model-request log",
)
@click.argument("day", required=False)
@click.option("--list", "list_days", is_flag=True, help="List the available log days.")
def logs_requests(day: str | None, list_days: bool) -> None:
    """Print one day's model-request log (DAY as YYYY-MM-DD, default
    today)."""
    paths = Paths.resolve()
    ctx = click.get_current_context()
    request_log = RequestLog(paths, _unlock(ctx, paths))

    if list_days:
        _echo_days(request_log.get_days(), "no request logs yet")
        return
    stamp = _day_stamp(ctx, day)
    if not request_log.get_path(stamp).exists():
        click.echo(f"no request log for {logs_view.dashed(stamp)}", err=True)
        ctx.exit(1)

    click.echo_via_pager(logs_view.render_requests(request_log, stamp))


@logs.command(
    "system",
    short_help="Show the background lore work",
)
@click.argument("day", required=False)
@click.option("--list", "list_days", is_flag=True, help="List the available log days.")
def logs_system(day: str | None, list_days: bool) -> None:
    """Print one day's system log — the lore worker's account of itself
    (DAY as YYYY-MM-DD, default today)."""
    paths = Paths.resolve()
    ctx = click.get_current_context()
    system_log = SystemLog(paths)
    if list_days:
        _echo_days(system_log.get_days(), "no system logs yet")
        return
    stamp = _day_stamp(ctx, day)
    path = system_log.get_path(stamp)
    if not path.exists():
        click.echo(f"no system log for {logs_view.dashed(stamp)}", err=True)
        ctx.exit(1)
    click.echo_via_pager(path.read_text(encoding="utf-8"))


@logs.command(
    "error",
    short_help="Show every contained crash's traceback",
)
@click.argument("day", required=False)
@click.option("--list", "list_days", is_flag=True, help="List the available log days.")
def logs_error(day: str | None, list_days: bool) -> None:
    """Print one day's error log — every contained crash's traceback
    (DAY as YYYY-MM-DD, default today)."""
    paths = Paths.resolve()
    ctx = click.get_current_context()
    error_log = ErrorLog(paths)
    if list_days:
        _echo_days(error_log.get_days(), "no error logs yet")
        return
    stamp = _day_stamp(ctx, day)
    path = error_log.get_path(stamp)
    if not path.exists():
        click.echo(f"no error log for {logs_view.dashed(stamp)}", err=True)
        ctx.exit(1)
    click.echo_via_pager(path.read_text(encoding="utf-8"))


def _unlock(ctx: click.Context, paths: Paths) -> crypto.Cipher:
    """The cipher for a subcommand, with the app's own load-and-unlock
    sequence and click-flavored errors."""
    try:
        return app_mod.unlock_cipher(app_mod.load_config(paths), paths)
    except config_mod.ConfigError as e:
        click.echo(f"config error: {e}", err=True)
        ctx.exit(2)
    except crypto.CryptoError as e:
        click.echo(str(e), err=True)
        ctx.exit(1)


def _day_stamp(ctx: click.Context, day: str | None) -> str:
    """The file stamp a subcommand pages: today when DAY is absent,
    `logs_view.resolve_day`'s parse otherwise — or a usage error."""
    if day is None:
        return datetime.now().astimezone().strftime("%Y%m%d")
    stamp = logs_view.resolve_day(day)
    if stamp is None:
        click.echo(
            "DAY must be YYYY-MM-DD (or YYYYMMDD), e.g. otaku logs requests 2026-07-25", err=True
        )
        ctx.exit(2)
    return stamp


def _echo_days(days: list[tuple[str, int]], empty: str) -> None:
    if not days:
        click.echo(empty)
        return
    for row in logs_view.day_rows(days):
        click.echo(row)
