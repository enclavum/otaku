"""The premise tab: the story's premise, one full-width text — no list
beside it — that Enter edits in place."""

from prompt_toolkit.formatted_text import StyleAndTextTuples

from otaku.terminal.screens.base import (
    wrap_text,
)
from otaku.terminal.screens.dossier.base import DossierBase


class PremiseTab(DossierBase):
    """The premise, drawn whole."""

    def _premise_text(self) -> StyleAndTextTuples:
        """The premise as wrapped read-only lines. The cursor is only the
        scroll anchor — no row is selected, the arrows just move through
        a long text — so the rows carry no band."""
        width = max(10, self._max_row_content_width() - 4)
        if not self.premise:
            self._premise_rows = 1
            return [("class:preview.muted", "  (no premise yet — enter to write one)")]
        lines = wrap_text(self.premise, width)
        self._premise_rows = len(lines)
        return [("class:preview.body", "".join(f"  {line}\n" for line in lines))]
