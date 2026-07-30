"""The TOML writing helpers.

Their contract is exactness: what `toml_key` and `toml_scalar` render must
parse back byte-for-byte with `tomllib` — the config files these build are
read by the app itself. `row` aligns a comment to one column so the
written files read as a table.
"""

import tomllib

from otaku.settings.files import row, toml_key, toml_scalar


class TestTomlKey:
    def test_a_simple_name_stays_bare(self) -> None:
        assert toml_key("ollama") == "ollama"
        assert toml_key("my-provider_2") == "my-provider_2"

    def test_a_name_with_dots_or_colons_is_quoted(self) -> None:
        key = toml_key("llama3:latest")
        parsed = tomllib.loads(f"[{key}]\n")
        assert list(parsed) == ["llama3:latest"]

    def test_a_quoted_name_survives_quotes_inside(self) -> None:
        key = toml_key('we"ird')
        parsed = tomllib.loads(f"[{key}]\n")
        assert list(parsed) == ['we"ird']


class TestTomlScalar:
    def test_booleans(self) -> None:
        assert toml_scalar(True) == "true"
        assert toml_scalar(False) == "false"

    def test_numbers_roundtrip(self) -> None:
        assert roundtrip(42) == 42
        assert roundtrip(1.5) == 1.5

    def test_a_plain_string_roundtrips(self) -> None:
        assert roundtrip("hello world") == "hello world"

    def test_quotes_backslashes_and_newlines_roundtrip(self) -> None:
        tricky = 'a "quoted" \\ path\nsecond line'
        assert roundtrip(tricky) == tricky


class TestRow:
    def test_aligns_the_comment_to_one_column(self) -> None:
        first = row("a = 1", "first")
        second = row("bbbb = 2", "second")
        assert first.index("#") == second.index("#")

    def test_a_long_setting_still_gets_a_separated_comment(self) -> None:
        long = row("a_very_long_setting_name_indeed = 12345", "note")
        assert long.endswith("  # note")

    def test_no_comment_means_the_setting_alone(self) -> None:
        assert row("a = 1", "") == "a = 1"


def roundtrip(value: object) -> object:
    return tomllib.loads(f"x = {toml_scalar(value)}")["x"]
