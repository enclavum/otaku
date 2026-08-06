"""Display formatting: framing composition, paths, one-line previews,
sizes, context windows."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from otaku.formatting import (
    combine_framing,
    flatten,
    format_context,
    format_size,
    human_age,
    pretty_path,
    printable,
    truncate,
)


class TestPrettyPath:
    def test_shortens_a_path_under_home(self) -> None:
        assert pretty_path(Path.home() / "notes" / "a.md") == "~/notes/a.md"

    def test_leaves_a_path_outside_home_alone(self) -> None:
        assert pretty_path(Path("/etc/hosts")) == "/etc/hosts"

    def test_shortens_home_itself(self) -> None:
        assert pretty_path(Path.home()) == "~"


class TestPrintable:
    def test_drops_the_controls_a_terminal_could_act_on(self) -> None:
        assert printable("a\x1b[2Ab\x07c\x00d\x7fe") == "a[2Abcde"

    def test_c1_controls_are_dropped(self) -> None:
        # xterm honors the 8-bit CSI/OSC aliases even in UTF-8 mode.
        assert printable("a\x9b2Ab\x85c\x9dd") == "a2Abcd"

    def test_newline_and_tab_survive(self) -> None:
        assert printable("line\nnext\tcol") == "line\nnext\tcol"

    def test_plain_text_and_unicode_pass_through(self) -> None:
        assert printable("Ombre parle — « oui »") == "Ombre parle — « oui »"


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


class TestCombineFraming:
    def test_a_turn_without_framing_is_its_body(self) -> None:
        assert combine_framing("I open the door.", None) == "I open the door."

    def test_a_placeholder_slots_the_body_in(self) -> None:
        framing = "((OOC: as Ryn.))\n{body}"
        assert combine_framing("I wait.", framing) == "((OOC: as Ryn.))\nI wait."

    def test_framing_without_a_placeholder_precedes_the_body(self) -> None:
        assert combine_framing("I wait.", "((OOC: note.))") == "((OOC: note.))\n\nI wait."

    def test_framing_alone_when_there_is_no_body(self) -> None:
        assert combine_framing("", "((OOC: you play Anna.))") == "((OOC: you play Anna.))"

    def test_other_braces_survive(self) -> None:
        framing = "((OOC: roll {2d6} for {name}.))\n{body}"
        assert combine_framing("I roll.", framing) == "((OOC: roll {2d6} for {name}.))\nI roll."

    def test_a_body_with_braces_survives(self) -> None:
        assert combine_framing("I say {hello}.", "note\n{body}") == "note\nI say {hello}."


class TestHumanAge:
    """Ages read like a human would say them, in the largest fitting unit."""

    def test_under_a_minute_is_just_now(self) -> None:
        assert human_age(datetime.now(UTC) - timedelta(seconds=5)) == "just now"

    def test_minutes(self) -> None:
        assert human_age(datetime.now(UTC) - timedelta(minutes=7)) == "7m ago"

    def test_hours(self) -> None:
        assert human_age(datetime.now(UTC) - timedelta(hours=3)) == "3h ago"

    def test_days(self) -> None:
        assert human_age(datetime.now(UTC) - timedelta(days=12)) == "12d ago"

    def test_accepts_an_aware_local_timestamp(self) -> None:
        local = (datetime.now(UTC) - timedelta(hours=2)).astimezone()
        assert human_age(local) == "2h ago"
