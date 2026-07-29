"""The pickers' shared text wrapping.

`wrap_text` wraps prose to a width while preserving blank lines — the
paragraph breaks the previews rely on — and never returns fewer lines
than the input has.
"""

from otaku.tui.screen import wrap_text


class TestWrapText:
    def test_wraps_to_the_width(self) -> None:
        assert wrap_text("aaa bbb ccc", 7) == ["aaa bbb", "ccc"]

    def test_preserves_blank_lines(self) -> None:
        assert wrap_text("one\n\ntwo", 10) == ["one", "", "two"]

    def test_short_text_stays_one_line(self) -> None:
        assert wrap_text("short", 40) == ["short"]

    def test_breaks_a_word_longer_than_the_width(self) -> None:
        lines = wrap_text("abcdefghij", 4)
        assert all(len(line) <= 4 for line in lines)
        assert "".join(lines) == "abcdefghij"

    def test_a_nonpositive_width_returns_the_text_unwrapped(self) -> None:
        assert wrap_text("anything at all", 0) == ["anything at all"]

    def test_empty_text_is_one_empty_line(self) -> None:
        assert wrap_text("", 10) == [""]
