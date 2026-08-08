"""Flattening pasted text for a one-line field."""

from otaku.terminal.clipboard import one_line


class TestOneLine:
    def test_a_trailing_newline_goes(self) -> None:
        assert one_line("https://example.test/v1\n") == "https://example.test/v1"

    def test_windows_line_endings_go(self) -> None:
        assert one_line("sk-abc\r\n") == "sk-abc"

    def test_an_embedded_break_closes_up(self) -> None:
        # A key wrapped across two lines by whatever it was copied from
        # is one key, not two — never a newline in the middle of a field.
        assert one_line("sk-abc\ndef") == "sk-abcdef"

    def test_the_edges_are_trimmed(self) -> None:
        assert one_line("  sk-abc  ") == "sk-abc"

    def test_plain_text_passes_through(self) -> None:
        assert one_line("sk-abc") == "sk-abc"

    def test_an_empty_clipboard_stays_empty(self) -> None:
        assert one_line("\n") == ""
