"""otaku command-line entry point.

`otaku` resumes the model and story the last session left off on and opens
the chat; on a first run — or when the remembered model's provider is gone
— it opens the model picker instead. `otaku logs` prints a day's
model-request log.
"""

import json
import re
from collections.abc import Iterator
from datetime import datetime

import click

from otaku import __version__, crypto
from otaku.chat import repl
from otaku.chat.state import Session
from otaku.formatting import pretty_path
from otaku.logs.requests import RequestLog
from otaku.paths import Paths
from otaku.providers.registry import Registry, autoconfigure_providers
from otaku.settings import config as config_mod
from otaku.settings import prompts as prompts_file
from otaku.settings import state as state_mod
from otaku.settings.files import write_atomic
from otaku.store import DatabaseError, Store, is_encrypted
from otaku.tui import models as model_picker
from otaku.tui import stories as story_picker


@click.group(invoke_without_command=True)
@click.version_option(__version__, "-v", "--version", prog_name="otaku")
@click.pass_context
def main(ctx: click.Context) -> None:
    """A roleplay terminal client."""
    if ctx.invoked_subcommand is not None:
        return
    paths = Paths.resolve()
    cfg = _load_config(ctx, paths)
    prompts_file.write_stub(paths)
    cipher = _unlock(ctx, cfg, paths)
    state = state_mod.load(paths)
    request_log = RequestLog(paths, cipher)
    provider_registry = Registry(
        cfg.providers, request_log=request_log, smooth=cfg.smooth_streaming
    )

    # A remembered model whose provider is still configured resumes straight
    # into the chat; anything else opens the picker. The picker runs before
    # the store: cancelling it exits without touching the database, and the
    # key ceremony above is done, so a passphrase prompt never lands after
    # the model is chosen.
    remembered = state.model if cfg.serves(state.model) else None
    spec = remembered or model_picker.pick(provider_registry)
    if spec is None:
        return

    try:
        store = Store.open(paths, cipher, backups=cfg.backups)
    except DatabaseError as e:
        click.echo(str(e), err=True)
        ctx.exit(1)
    try:
        session = Session.start(
            config=cfg,
            paths=paths,
            providers=provider_registry,
            spec=spec,
            state=state,
            store=store,
            pick_model=lambda current: model_picker.pick(provider_registry, initial_spec=current),
            pick_story=story_picker.pick,
        )
        repl.run(session, store)
    finally:
        store.close()


@main.command()
@click.argument("day", required=False)
@click.option("--list", "list_days", is_flag=True, help="List the available log days.")
def logs(day: str | None, list_days: bool) -> None:
    """Print one day's model-request log (DAY as YYYYMMDD, default today)."""
    paths = Paths.resolve()
    ctx = click.get_current_context()
    cfg = _load_config(ctx, paths)
    cipher = _unlock(ctx, cfg, paths)
    request_log = RequestLog(paths, cipher)

    if list_days:
        days = request_log.get_days()
        if not days:
            click.echo("no request logs yet")
            return
        for name, size in days:
            click.echo(f"{name}  {size:>10,} B")
        return

    if day and not re.fullmatch(r"\d{8}", day):
        click.echo("DAY must be YYYYMMDD, e.g. otaku logs 20260725", err=True)
        ctx.exit(2)
    stamp = day or datetime.now().astimezone().strftime("%Y%m%d")
    if not request_log.get_path_for(stamp).exists():
        click.echo(f"no request log for {stamp}", err=True)
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


def _load_config(ctx: click.Context, paths: Paths) -> config_mod.Config:
    paths.ensure_tree()
    if not paths.config_file.exists():
        first_run = config_mod.Config(providers=autoconfigure_providers())
        write_atomic(paths.config_file, first_run.to_toml())
        click.echo(f"Created {pretty_path(paths.config_file)}")
    try:
        return config_mod.load(paths)
    except config_mod.ConfigError as e:
        click.echo(f"config error: {e}", err=True)
        ctx.exit(2)


def _unlock(ctx: click.Context, cfg: config_mod.Config, paths: Paths) -> crypto.Cipher:
    # An encrypted database without its keystore must be refused BEFORE the
    # key ceremony: unlock would otherwise mint a fresh key over it, making
    # every sealed row permanently unreadable.
    if (
        cfg.encryption.provider != "none"
        and is_encrypted(paths.database_file) is True
        and not paths.keys_file.exists()
    ):
        click.echo(
            f"{pretty_path(paths.keys_file)} is missing, but "
            f"{pretty_path(paths.database_file)} is encrypted. Its content can only "
            "be read with the key that keystore holds — restore it from backup "
            "(together with its KEK), or move the database aside.",
            err=True,
        )
        ctx.exit(1)
    try:
        return crypto.unlock(cfg.encryption, paths)
    except crypto.CryptoError as e:
        click.echo(f"Could not unlock encryption: {e}", err=True)
        ctx.exit(1)
