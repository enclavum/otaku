"""The answers to the question the story stands on, drawn ABOVE the
prompt line as rows of its own message (`AnswerMenu`)."""

from collections.abc import Callable, Sequence

from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text import StyleAndTextTuples

from otaku.terminal.tty.render import BAR, block

# The blank before a prompt that opens on a question's answers, while
# they show: the bar alone, the question's block running on into the rows.
_BARRED = BAR.rstrip()


class AnswerMenu:
    """The answers to the question the story stands on, above the prompt
    line: the rows of the prompt's MESSAGE, behind the bar the question
    stands behind (`render.BAR`, wrapped to the width as its block is),
    numbered as the model listed them, the one under the cursor starred
    and in the accent, redrawn as ↑/↓ move it, a blank line before the
    prompt.
    The blank line before the prompt is the menu's own first row while
    the loop leaves it to the menu (`top_row`, the ledger's deferred
    gap): the bar alone while the rows show, so the question's block
    runs on into them, a plain blank once they hide. Shown on the empty
    line alone — a character typed is the reader's own answer and the
    rows go, the empty line brings them back — and
    gone once the line is sent, so the scrollback keeps the
    question alone, as a past question is shown. Enter sends the answer
    under the cursor as the line; → takes it into the line to be edited;
    Esc dismisses the rows for this prompt. `answers` is asked as each
    prompt opens (`open`, its `pre_run`): the question is the newest
    reply's, and the line played answers it."""

    def __init__(self, answers: Callable[[], Sequence[str]]) -> None:
        self._answers = answers
        self.rows: tuple[str, ...] = ()
        self.choice = 0
        self.dismissed = False
        self.top_row = False  # the blank before the prompt is this menu's to draw
        # Whether this prompt may offer the answers at all: the one after
        # a reply, or after the resumed story's echo — never after a
        # command, whose output stands between the question and the rows.
        self.offered = True

    def open(self) -> None:
        self.rows = tuple(self._answers()) if self.offered else ()
        self.choice = 0
        self.dismissed = False

    @property
    def posed(self) -> bool:
        """Whether the story stands on a question with answers to offer —
        what the next prompt will open with, asked before it opens: the
        loop then leaves the blank before the prompt to the menu
        (`top_row`)."""
        return self.offered and bool(self._answers())

    def shown(self) -> bool:
        app = get_app()
        return bool(self.rows) and not self.dismissed and not app.current_buffer.text

    def chosen(self) -> str:
        return self.rows[self.choice]

    def move(self, step: int) -> None:
        self.choice = (self.choice + step) % len(self.rows)

    def message(self, prefix: str) -> StyleAndTextTuples:
        """The prompt's message: the answers as rows over the prefix while
        they are shown, the prefix alone otherwise — and alone again in
        the final render, once the line is sent (`is_done`), so the rows
        leave the screen with the menu."""
        out: StyleAndTextTuples = []
        showing = self.shown() and not get_app().is_done
        if self.top_row:
            out.append(("class:answer", _BARRED + "\n") if showing else ("", "\n"))
        if showing:
            # Behind the bar, the chosen row marked with a star in the two
            # columns after it; an answer longer than a row wraps behind
            # the bar and past the mark; a blank line before the prompt.
            width = get_app().output.get_size().columns
            for i, answer in enumerate(self.rows):
                chosen = i == self.choice
                style = "class:answer.current" if chosen else "class:answer"
                rows = block(f"{i + 1}. {answer}", width - 2).split("\n")
                for n, row in enumerate(rows):
                    mark = "*" if chosen and n == 0 else " "
                    out.append(("class:answer", BAR))
                    out.append((style, f"{mark} {row[len(BAR) :]}\n"))
            out.append(("", "\n"))
        out.append(("class:prompt", prefix))
        return out
