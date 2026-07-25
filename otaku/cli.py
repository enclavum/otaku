"""otaku command-line entry point."""

import click

from otaku import __version__


@click.group(invoke_without_command=True)
@click.version_option(__version__, "-v", "--version", prog_name="otaku")
@click.pass_context
def main(ctx: click.Context) -> None:
    """An interactive roleplay client for local model servers."""
    if ctx.invoked_subcommand is None:
        click.echo("otaku: the chat client arrives in a later build step")
