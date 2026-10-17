"""The /help layout: what the terminal's own rendering promises at any
width. Two columns when the split saves real height, one otherwise; a
description wraps inside its column rather than being cut; and whatever
the width, every command is still on the page.

The look is not asserted — running the app shows that. What running it
cannot show is the layout holding at widths nobody opens a terminal at,
and no command falling out of a column at one of them.
"""

from otaku.terminal import help
from otaku.terminal.commands import COMMANDS, SHORTCUTS

WIDTHS = (60, 80, 100, 120, 130, 140, 160, 200, 240)


class TestFits:
    def test_no_line_outruns_the_width(self) -> None:
        for width in WIDTHS:
            longest = max(len(line) for line in _page(width).splitlines())
            assert longest <= width, f"{width}: a line ran to {longest}"

    def test_the_widest_column_is_still_used(self) -> None:
        # The layout fills the terminal it was given rather than settling
        # for a narrow column in the middle of a wide screen.
        for width in (140, 200):
            longest = max(len(line) for line in _page(width).splitlines())
            assert longest > width * 0.7, width


class TestKeepsEverything:
    def test_every_command_appears_at_every_width(self) -> None:
        for width in WIDTHS:
            text = _page(width)
            for spec in COMMANDS:
                assert spec.token in text, f"{spec.token} missing at {width}"

    def test_no_description_is_cut(self) -> None:
        # Wrapped, never truncated: every description's words survive in
        # order, whatever the width. The one exception is a token longer
        # than the narrowest column can ever be — it has to break,
        # because the width is a promise and the token is a value list.
        for width in WIDTHS:
            words = _page(width).split()
            for spec in COMMANDS:
                kept = [word for word in spec.description.split() if len(word) <= 26]
                assert _subsequence(kept, words), f"{spec.token} at {width}"


class TestColumns:
    def test_a_wide_terminal_takes_two_columns(self) -> None:
        assert len(_page(200).splitlines()) < len(_page(120).splitlines()) * 0.8

    def test_a_narrow_terminal_stays_in_one(self) -> None:
        # Two columns of a 15-character description would read worse than
        # the tall single column, so the split is not taken.
        assert max(len(line) for line in _page(80).splitlines()) <= 80
        assert "Playing:" in _page(80).splitlines()[0]

    def test_growing_the_terminal_never_costs_height(self) -> None:
        heights = [len(_page(width).splitlines()) for width in WIDTHS]
        assert heights == sorted(heights, reverse=True), heights


def _page(width: int) -> str:
    """The page as the app builds it — the real captions, an asked width."""
    return help.text({token: shortcut.caption for token, shortcut in SHORTCUTS.items()}, width)


def _subsequence(needle: list[str], haystack: list[str]) -> bool:
    """Whether `needle`'s items appear in `haystack` in order."""
    it = iter(haystack)
    return all(word in it for word in needle)
