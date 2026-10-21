"""A story's next request, typed, carried out — the twin of
`backend.story.context`: previewed in a pager (`/context`) — the report's
`text()`, the role markers dimmed."""

import click

from otaku.terminal.chat.chat import Chat
from otaku.terminal.tty import DIM, RESET


def context(chat: Chat, raw: str) -> None:
    """Page the preview (long stories run to thousands of lines), the
    role markers dimmed on the way through. `otaku logs` shows what was
    actually sent; this shows what is about to be."""
    preview = chat.api.stories.context.get(chat.played()).text(dim=DIM, reset=RESET)
    # The pager holds the real stream; its output is past the ledger.
    chat.ledger.invalidate()
    # color=True keeps the dim markers through less (-R).
    click.echo_via_pager(preview, color=True)
