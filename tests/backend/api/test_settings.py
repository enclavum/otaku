"""The /set family's pure edges: how stop strings are typed and read
back, and how a value is shown."""

import pytest

from otaku.backend.api.settings import parameter_text, parse_stops


class TestParseStops:
    def test_a_bare_text_is_one_stop_as_it_is(self) -> None:
        assert parse_stops("END") == ["END"]
        assert parse_stops("  The End  ") == ["The End"]

    def test_quoted_stops_are_several_and_may_hold_a_newline(self) -> None:
        assert parse_stops('"\\nUser:" "END"') == ["\nUser:", "END"]
        assert parse_stops('"a b"   "c"') == ["a b", "c"]

    def test_an_open_quote_or_an_empty_stop_is_refused(self) -> None:
        for raw in ('"unclosed', '"" "x"', "", '"a" b'):
            with pytest.raises(ValueError):
                parse_stops(raw)

    def test_what_is_shown_reads_back_the_same(self) -> None:
        stops = ["\nUser:", "END", 'say "no"']
        assert parse_stops(parameter_text(stops)) == stops


class TestParameterText:
    def test_a_number_is_itself(self) -> None:
        assert parameter_text(0.7) == "0.7"
        assert parameter_text(40) == "40"

    def test_stops_are_json_strings_apart(self) -> None:
        assert parameter_text(["\nUser:", "END"]) == '"\\nUser:" "END"'
