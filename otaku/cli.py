"""otaku command-line entry point.

`otaku` resumes the model and story the last session left off on and opens
the chat; on a first run — or when the remembered model's provider is gone
— it opens the model picker instead. `otaku logs requests` prints a day's
model-request log, `otaku logs system` the lore worker's own account.
"""

import json
import re
from collections.abc import Iterator
from datetime import datetime

import click

from otaku import __version__, crypto
from otaku import app as app_mod
from otaku.logs.requests import RequestLog
from otaku.logs.system import SystemLog
from otaku.paths import Paths
from otaku.settings import config as config_mod
from otaku.store import DatabaseError


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-v", "--version", prog_name="otaku")
@click.pass_context
def main(ctx: click.Context) -> None:
    """A roleplay terminal client."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        application = app_mod.App()
    except app_mod.CancelledError:
        return
    except config_mod.ConfigError as e:
        click.echo(f"config error: {e}", err=True)
        ctx.exit(2)
    except (crypto.CryptoError, DatabaseError) as e:
        click.echo(str(e), err=True)
        ctx.exit(1)
    try:
        application.run()
    finally:
        application.close()


@main.group(invoke_without_command=True)
@click.pass_context
def logs(ctx: click.Context) -> None:
    """Day-rotated logs: `requests` (what the models were sent — the
    default) and `system` (the lore worker's own account)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(logs_requests)


@logs.command("requests")
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
    stamp = _resolve_day(ctx, day)
    if not request_log.get_path(stamp).exists():
        click.echo(f"no request log for {_dashed(stamp)}", err=True)
        ctx.exit(1)

    def render() -> Iterator[str]:
        for entry in request_log.read(stamp):
            yield f"=== {entry.ts}  {entry.provider}  [{entry.purpose}]\n"
            if entry.body is None:
                yield "  <unreadable: wrong key or corrupted>\n\n"
                continue
            meta = {k: v for k, v in entry.body.items() if k != "messages"}
            yield f"  {json.dumps(meta, ensure_ascii=False)}\n"
            messages = entry.body.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    if isinstance(message, dict):
                        yield f"  [{message.get('role')}] {message.get('content')}\n"
            yield "\n"

    click.echo_via_pager(render())


@logs.command("system")
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
    stamp = _resolve_day(ctx, day)
    path = system_log.get_path(stamp)
    if not path.exists():
        click.echo(f"no system log for {_dashed(stamp)}", err=True)
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


def _resolve_day(ctx: click.Context, day: str | None) -> str:
    """A DAY argument as the logs name their files (YYYYMMDD). Accepts the
    dashed form too; defaults to today."""
    if day is None:
        return datetime.now().astimezone().strftime("%Y%m%d")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return day.replace("-", "")
    if re.fullmatch(r"\d{8}", day):
        return day
    click.echo(
        "DAY must be YYYY-MM-DD (or YYYYMMDD), e.g. otaku logs requests 2026-07-25", err=True
    )
    ctx.exit(2)


def _dashed(stamp: str) -> str:
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"


def _echo_days(days: list[tuple[str, int]], empty: str) -> None:
    if not days:
        click.echo(empty)
        return
    for name, size in days:
        click.echo(f"{_dashed(name)}  {size:>10,} B")
