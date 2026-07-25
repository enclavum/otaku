"""otaku command-line entry point."""

import click

from otaku import __version__, crypto
from otaku.paths import Paths
from otaku.settings import config as config_mod
from otaku.settings import state as state_mod
from otaku.settings.files import write_atomic
from otaku.term.text import pretty_path


@click.group(invoke_without_command=True)
@click.version_option(__version__, "-v", "--version", prog_name="otaku")
@click.pass_context
def main(ctx: click.Context) -> None:
    """A roleplay terminal client."""
    if ctx.invoked_subcommand is not None:
        return
    paths = Paths.resolve()
    paths.ensure_tree()
    if not paths.config_file.exists():
        write_atomic(paths.config_file, config_mod.Config.default().to_toml())
        click.echo(f"Created {pretty_path(paths.config_file)}")
    try:
        cfg = config_mod.load(paths)
    except config_mod.ConfigError as e:
        click.echo(f"config error: {e}", err=True)
        ctx.exit(2)
    try:
        cipher = crypto.unlock(cfg.encryption, paths)
    except crypto.CryptoError as e:
        click.echo(f"Could not unlock encryption: {e}", err=True)
        ctx.exit(1)
    state = state_mod.load(paths)

    # Session preflight, shown until the chat client exists.
    plain = isinstance(cipher, crypto.PlainCipher)
    encryption = "none (plain text)" if plain else cfg.encryption.provider
    click.echo(f"State dir:  {pretty_path(paths.root)}")
    click.echo(f"Providers:  {', '.join(sorted(cfg.providers))}")
    click.echo(f"Encryption: {encryption}")
    click.echo(f"Resume:     model={state.model or '(none)'} story={state.story or '(none)'}")
