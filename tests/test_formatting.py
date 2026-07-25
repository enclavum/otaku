"""Display formatting: paths, one-line previews, sizes, context windows."""

from pathlib import Path

from otaku.formatting import (
    flatten,
    format_context,
    format_size,
    pretty_path,
    truncate,
)


class TestPrettyPath:
    def test_shortens_a_path_under_home(self) -> None:
        assert pretty_path(Path.home() / "notes" / "a.md") == "~/notes/a.md"

    def test_leaves_a_path_outside_home_alone(self) -> None:
        assert pretty_path(Path("/etc/hosts")) == "/etc/hosts"

    def test_shortens_home_itself(self) -> None:
        assert pretty_path(Path.home()) == "~"


class TestFlatten:
    def test_turns_newlines_into_spaces(self) -> None:
        assert flatten("one\ntwo") == "one two"

    def test_turns_tabs_into_spaces(self) -> None:
        assert flatten("one\ttwo") == "one two"

    def test_strips_the_edges(self) -> None:
        assert flatten("  padded \n") == "padded"

    def test_collapses_runs_of_whitespace(self) -> None:
        assert flatten("one\n\ntwo") == "one two"

    def test_leaves_plain_text_alone(self) -> None:
        assert flatten("a plain line") == "a plain line"


class TestTruncate:
    def test_leaves_short_text_alone(self) -> None:
        assert truncate("short", 10) == "short"

    def test_leaves_exactly_fitting_text_alone(self) -> None:
        assert truncate("12345", 5) == "12345"

    def test_marks_a_cut_with_an_ellipsis(self) -> None:
        assert truncate("abcdefgh", 5) == "abcd…"

    def test_never_exceeds_the_limit(self) -> None:
        for limit in range(0, 8):
            assert len(truncate("abcdefgh", limit)) <= limit


class TestFormatSize:
    def test_renders_gigabytes_with_one_decimal(self) -> None:
        assert format_size(30 * 1024**3) == "30.0 GB"

    def test_rounds_to_one_decimal(self) -> None:
        assert format_size(int(4.25 * 1024**3)) == "4.2 GB"

    def test_shows_a_dash_when_unknown(self) -> None:
        assert format_size(None) == "—"

    def test_shows_a_dash_for_zero(self) -> None:
        assert format_size(0) == "—"


class TestFormatContext:
    def test_uses_k_for_multiples_of_1024(self) -> None:
        assert format_context(8192) == "8K"

    def test_uses_m_for_multiples_of_a_mebi(self) -> None:
        assert format_context(1_048_576) == "1M"

    def test_uses_k_for_large_multiples_of_1024(self) -> None:
        assert format_context(262_144) == "256K"

    def test_falls_back_to_a_thousands_separated_number(self) -> None:
        assert format_context(1500) == "1,500"

    def test_is_empty_when_unknown(self) -> None:
        assert format_context(None) == ""
        assert format_context(0) == ""
