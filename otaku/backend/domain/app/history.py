"""The lines submitted before — what a prompt's ↑/↓ walks, store-backed
so it survives a launch and is shared by both frontends. Best effort
both ways: a store hiccup is an empty history, never a broken prompt,
and a line the store cannot take changes nothing."""

import contextlib

from otaku.backend.domain.app import App


def list_history_lines(app: App) -> list[str]:
    """The lines submitted before, most recent first."""
    try:
        return app._store.history.get_recent()
    except Exception:
        return []


def create_history_line(app: App, text: str) -> None:
    """One submitted line into the history — blanks and immediate
    repeats are skipped."""
    with contextlib.suppress(Exception):
        app._store.history.add(text)
