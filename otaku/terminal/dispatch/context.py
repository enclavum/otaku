"""A story's next request, typed, carried out — the twin of
`backend.story.context`: previewed in a pager (`/context`) — the report's
`text()`, its markers dimmed and its sections ruled."""

import click

from otaku.terminal.chat.chat import Chat
from otaku.terminal.tty import DIM, RESET, break_rule
from otaku.terminal.tty.cursor import terminal_width


def context(chat: Chat, raw: str) -> None:
    """Page the preview (long stories run to thousands of lines), the
    markers dimmed on the way through and each section set apart by the
    break rule, across the screen. `otaku logs` shows what was actually
    sent; this shows what is about to be."""
    report = chat.api.stories.context.get(chat.played())
    preview = report.text(dim=DIM, reset=RESET, rule=break_rule(terminal_width()))
    # The pager holds the real stream; its output is past the ledger.
    chat.ledger.invalidate()
    # color=True keeps the dim markers through less (-R).
    click.echo_via_pager(preview, color=True)
