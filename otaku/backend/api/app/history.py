"""The lines submitted before (`HistoryResource`) — what a prompt's ↑/↓
walks, store-backed so it survives a launch and is shared by both
frontends."""

from __future__ import annotations

import builtins

from otaku.backend.api._writes import Changed
from otaku.backend.domain.app import App
from otaku.backend.domain.app import history as domain_history


class HistoryResource:
    """/history — the lines submitted before."""

    def __init__(self, app: App) -> None:
        self._app = app

    def list(self) -> list[str]:
        """The lines, most recent first. Best effort: a store hiccup is
        an empty history, never a broken prompt."""
        return domain_history.list_history_lines(self._app)

    def create(self, *, text: str) -> Changed[builtins.list[str]]:
        """One submitted line into the history (blanks and immediate
        repeats are skipped), answered with the history as it now
        stands. Best effort: a store that cannot take it changes
        nothing."""
        domain_history.create_history_line(self._app, text)
        return Changed(self.list(), "Kept in the history.")
