"""Keyboard-layout folding.

The contract: `latin_key` returns the Latin character on the same physical
key — a Cyrillic (ЙЦУКЕН) letter maps to its QWERTY twin, anything else
comes back lowercased and otherwise untouched. It works per character, so
a whole typed word folds too.
"""

from otaku.term.keys import latin_key


class TestLatinKey:
    def test_maps_a_cyrillic_letter_to_its_physical_twin(self) -> None:
        assert latin_key("н") == "y"
        assert latin_key("т") == "n"
        assert latin_key("у") == "e"  # noqa: RUF001
        assert latin_key("д") == "l"
        assert latin_key("г") == "u"  # noqa: RUF001

    def test_lowercases_before_mapping(self) -> None:
        assert latin_key("Н") == "y"  # noqa: RUF001
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
