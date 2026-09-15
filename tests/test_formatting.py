"""Display formatting: paths, one-line previews, sizes, context windows."""

import tomllib
from pathlib import Path

from otaku.formatting import (
    decode_text,
    flatten,
    format_context,
    format_seconds,
    format_size,
    pretty_path,
    printable,
    render,
    toml_key,
    toml_scalar,
    truncate,
    truncate_label,
)


class TestDecodeText:
    def test_utf8_reads_as_itself(self) -> None:
        assert decode_text("…and on which port".encode()) == "…and on which port"

    def test_a_windows_ansi_file_reads_instead_of_raising(self) -> None:
        # 0x85 is "…" in cp1252 (and cp1251 alike): what 0.3.0's
        # unencoded writer left on native Windows.
        assert "…" in decode_text("…and on which port".encode("cp1252"))

    def test_no_bytes_can_make_it_raise(self) -> None:
        # 0x81 is undefined even in cp1252 — latin-1 still answers.
        assert decode_text(b"\xff\x81ok").endswith("ok")

    def test_newlines_normalize_as_read_text_always_did(self) -> None:
        # A 0.3.0 on Windows wrote CRLF; a stray \r is an invalid
        # character to a TOML parser, so none may survive the read —
        # including the \r\r\n a healing write once mangled through
        # Windows' own newline translation.
        assert decode_text(b"a\r\nb\rc") == "a\nb\nc"
        assert decode_text("# …\r\r\n[x]\r\n".encode("cp1252")) == "# …\n\n[x]\n"


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


class TestFormatSeconds:
    def test_whole_seconds_from_ten_up(self) -> None:
        assert format_seconds(30) == "30 seconds"
        assert format_seconds(10.4) == "10 seconds"

    def test_tenths_below_ten(self) -> None:
        assert format_seconds(0.5) == "0.5 seconds"
        assert format_seconds(2.5) == "2.5 seconds"

    def test_a_round_figure_drops_its_tenth(self) -> None:
        assert format_seconds(5.0) == "5 seconds"

    def test_one_second_is_singular(self) -> None:
        assert format_seconds(1.0) == "1 second"


class TestFormatContext:
    def test_a_round_decimal_size_keeps_the_label_it_is_sold_under(self) -> None:
        # 128,000 divides by 1024 too — the decimal reading has to win, or
        # the 128K every catalog advertises shows as 125K.
        assert format_context(128_000) == "128K"
        assert format_context(200_000) == "200K"
        assert format_context(64_000) == "64K"
        assert format_context(1_000_000) == "1M"

    def test_a_power_of_two_size_takes_the_label_its_vendor_prints(self) -> None:
        assert format_context(8192) == "8K"
        assert format_context(131_072) == "128K"
        assert format_context(262_144) == "256K"
        assert format_context(1_048_576) == "1M"
        assert format_context(2_097_152) == "2M"

    def test_anything_else_rounds_to_a_whole_one(self) -> None:
        assert format_context(1_047_576) == "1M"
        assert format_context(163_839) == "164K"

    def test_a_size_under_a_thousand_stands_as_it_is(self) -> None:
        assert format_context(512) == "512"

    def test_every_catalog_size_fits_a_narrow_column(self) -> None:
        # The picker holds this column at a FIXED width, so the promise
        # is the width, not just the wording.
        sizes = (
            4096, 8192, 32_768, 32_000, 64_000, 65_536, 96_000, 128_000, 131_072,
            163_840, 164_000, 200_000, 262_144, 1_000_000, 1_047_576, 1_048_576,
            2_097_152, 10_000_000,
        )  # fmt: skip
        assert max(len(format_context(n)) for n in sizes) <= 4

    def test_is_empty_when_unknown(self) -> None:
        assert format_context(None) == ""
        assert format_context(0) == ""


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

    def test_control_characters_are_escaped(self) -> None:
        # Keys are values too — a model name heads its models.toml table,
        # and one raw control byte would unparse the whole file.
        tricky = "bad\nname\x01\x7f"
        key = toml_key(tricky)
        parsed = tomllib.loads(f"[{key}]\n")
        assert list(parsed) == [tricky]


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

    def test_control_characters_roundtrip(self) -> None:
        # A server-reported model name can carry anything; whatever the
        # string holds, the rendered file must parse back.
        tricky = "a\rb\tc\x01d\x7fe\x1bf"
        assert roundtrip(tricky) == tricky


class TestRender:
    def test_fills_the_given_placeholders(self) -> None:
        assert render("Hi {name}, {word}.", name="Ana", word="welcome") == "Hi Ana, welcome."

    def test_other_braces_stay_literal(self) -> None:
        template = 'Reply as {"scene": {"title": "..."}} for {name}'
        assert render(template, name="x") == 'Reply as {"scene": {"title": "..."}} for x'

    def test_an_unknown_placeholder_is_just_text(self) -> None:
        assert render("{name} and {unknown}", name="x") == "x and {unknown}"

    def test_substituted_text_is_never_rescanned(self) -> None:
        assert render("{a} {b}", a="{b}", b="two") == "{b} two"

    def test_repeated_placeholders_all_fill(self) -> None:
        assert render("{n}-{n}", n="x") == "x-x"

    def test_no_substitutions_return_the_template(self) -> None:
        assert render("{anything} stays") == "{anything} stays"


def roundtrip(value: object) -> object:
    return tomllib.loads(f"x = {toml_scalar(value)}")["x"]


class TestTruncateLabel:
    def test_a_short_label_stays(self) -> None:
        assert truncate_label("The River", 50) == "The River"

    def test_a_long_label_is_cut_with_an_ellipsis(self) -> None:
        assert truncate_label("x" * 60, 50) == "x" * 49 + "…"

    def test_the_fork_number_survives_the_cut(self) -> None:
        label = truncate_label("A" * 60 + " - 3", 50)
        assert label.endswith("… - 3") and len(label) == 50

    def test_newlines_flatten_before_the_cut(self) -> None:
        assert truncate_label("one\ntwo", 50) == "one two"
