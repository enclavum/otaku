"""What may reach the terminal from the tail under `otaku serve`.

The lines it draws are made from bytes off a socket — any page in any
browser can ask a loopback server for a path of escape sequences — so
nothing in one may be a sequence the terminal obeys. The tail rewrites
itself by moving the cursor, and a line that can move it too would
rewrite the address above it.

The rule lives inside `Ticker.show`, so that is what is driven here —
against a buffer that says it is a terminal. Nothing else of one is
involved: the rows are written into memory and read back as text.
"""

import contextlib
import io

import pytest

from otaku.console import ERASE_BELOW
from otaku.console.ticker import Ticker


class TestWhatIsShown:
    def test_an_ordinary_request_line_is_untouched(self) -> None:
        assert "GET /api/turns 200" in _drawn("GET /api/turns 200")

    def test_an_escape_cannot_survive(self) -> None:
        # The tail's own erase is the only sequence in a draw; nothing
        # the line carried may be a second one.
        drawn = _drawn("GET /\x1b[2J\x1b[31m 404").replace(ERASE_BELOW, "")
        assert "\x1b" not in drawn

    def test_a_newline_cannot_survive(self) -> None:
        # One line is one row: a line break would push the rows below it
        # down and leave the redraw counting the wrong number back up.
        drawn = _drawn("GET /a\nb 404").rstrip("\n")
        assert "\n" not in drawn
        assert "\r" not in drawn

    def test_what_is_dropped_leaves_a_mark(self) -> None:
        # Replaced, not deleted: a path that was doing something odd
        # should still look odd in the terminal.
        assert "GET /· 404" in _drawn("GET /\x07 404")

    def test_the_app_s_own_punctuation_is_printable(self) -> None:
        # The sentences that come through here carry otaku's own
        # typography, and none of it is a control character.
        said = "web request — RuntimeError, in ~/.otaku/logs"
        assert said in _drawn(said)


class _Screen(io.StringIO):
    """Stdout as the tail requires it: something that says it is a
    terminal, because a tail draws nothing down a pipe."""

    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _no_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every escape in a result then came from the line and not from the
    tail's own dimming."""
    monkeypatch.setenv("NO_COLOR", "1")


def _drawn(line: str) -> str:
    """One line through the tail, and what the terminal would have got.
    The tail settles at construction whether it may draw at all, so it
    is made inside the swap."""
    written = _Screen()
    with contextlib.redirect_stdout(written):
        Ticker().show(line)
    return written.getvalue()
