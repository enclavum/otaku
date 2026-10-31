"""Naming a story: the numbering every way of making one goes through —
a fork of another, a document imported twice, a title typed twice. And
counting a message's words: a run between spaces holding a letter or a
digit, each Chinese or Japanese character a word of its own."""

from otaku.store.ops.stories import _count_words, unique_title


class TestUniqueTitle:
    def test_an_unnumbered_title_starts_at_two(self) -> None:
        assert unique_title("The River", set()) == "The River - 2"

    def test_a_numbered_title_counts_up_from_its_own_number(self) -> None:
        # Not "the first free from 2": a fork must never be numbered
        # below the story it was made from, however many gaps stand open.
        assert unique_title("The River - 5", {"The River - 2", "The River - 3"}) == "The River - 6"

    def test_the_numbering_is_replaced_never_appended(self) -> None:
        assert unique_title("The River - 3", set()) == "The River - 4"

    def test_a_pile_of_numbering_collapses_to_the_stem(self) -> None:
        assert unique_title("The River - 3 - 3 - 2", set()) == "The River - 3"

    def test_a_taken_name_is_skipped(self) -> None:
        taken = {"The River - 2", "The River - 3", "The River - 4"}
        assert unique_title("The River", taken) == "The River - 5"

    def test_only_a_trailing_number_counts_as_numbering(self) -> None:
        assert unique_title("Chapter 7", set()) == "Chapter 7 - 2"
        assert unique_title("Book 2 of 3", set()) == "Book 2 of 3 - 2"

    def test_a_title_that_is_only_a_number_keeps_itself(self) -> None:
        assert unique_title(" - 2", set()) == " - 2 - 2"

    def test_a_non_latin_title_numbers_like_any_other(self) -> None:
        assert unique_title("Река — 2", set()) == "Река — 2 - 2"  # an em dash is not the separator
        assert unique_title("Река - 2", set()) == "Река - 3"

    def test_an_untitled_source_forks_untitled(self) -> None:
        assert unique_title("", set()) is None


class TestCountWords:
    def test_a_word_is_a_run_between_spaces(self) -> None:
        assert _count_words("The keeper waits at the gate.") == 6
        assert _count_words("Room 14, then\nthe stair.") == 5

    def test_a_run_without_a_letter_or_a_digit_is_no_word(self) -> None:
        assert _count_words("I open — the door. * * *") == 4
        assert _count_words("___ ...") == 0

    def test_nothing_counts_none(self) -> None:
        assert _count_words("") == 0
        assert _count_words(" \n\t ") == 0

    def test_a_chinese_or_japanese_character_is_a_word(self) -> None:
        assert _count_words("雨が降る") == 4
        assert _count_words("下雨了") == 3
        assert _count_words("I said 雨 twice") == 4

    def test_korean_counts_its_words_as_they_stand(self) -> None:
        assert _count_words("비가 온다") == 2
