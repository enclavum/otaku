"""Keyboard-layout folding, and the color vocabulary.

`latin_key`'s contract: it returns the Latin character on the same physical
key — a Cyrillic (ЙЦУКЕН) letter maps to its QWERTY twin, anything else
comes back lowercased and otherwise untouched. It works per character, so
a whole typed word folds too.

`color`'s contract: a name becomes a 16-slot palette escape (portable, and
shaded by the reader's theme), a #rrggbb becomes truecolor, and anything
else becomes "" so the caller can fall back to its own default.
"""

from otaku.terminal import color, latin_key


class TestLatinKey:
    def test_maps_a_cyrillic_letter_to_its_physical_twin(self) -> None:
        assert latin_key("н") == "y"
        assert latin_key("т") == "n"
        assert latin_key("у") == "e"
        assert latin_key("д") == "l"
        assert latin_key("г") == "u"

    def test_lowercases_before_mapping(self) -> None:
        assert latin_key("Н") == "y"
        assert latin_key("E") == "e"

    def test_leaves_latin_untouched(self) -> None:
        assert latin_key("y") == "y"
        assert latin_key("e") == "e"

    def test_leaves_digits_and_punctuation_untouched(self) -> None:
        assert latin_key("3") == "3"
        assert latin_key("/") == "/"

    def test_folds_a_whole_typed_word(self) -> None:
        assert latin_key("нуы") == "yes"
        assert latin_key("тщ") == "no"

    def test_empty_stays_empty(self) -> None:
        assert latin_key("") == ""


class TestColor:
    def test_a_name_becomes_a_palette_slot(self) -> None:
        assert color("cyan") == "\x1b[36m"
        assert color("blue") == "\x1b[34m"
        assert color("bright blue") == "\x1b[94m"

    def test_names_are_read_loosely(self) -> None:
        # However someone spells it in a hand-edited config.
        assert color("Cyan") == color(" cyan ") == color("cyan")
        assert color("bright-blue") == color("bright_blue") == color("Bright  Blue")

    def test_a_hex_becomes_truecolor(self) -> None:
        assert color("#5869f6") == "\x1b[38;2;88;105;246m"
        assert color("#5869F6") == color("#5869f6")

    def test_anything_else_is_empty(self) -> None:
        # The caller falls back to its default; nothing is printed at the reader.
        assert color("chartreuse") == ""
        assert color("#12345") == ""
        assert color("#gggggg") == ""
        assert color("") == ""
