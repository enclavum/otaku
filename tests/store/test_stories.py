"""Naming a fork: the numbering `stories.fork` gives a copy."""

from otaku.store.stories import fork_title


class TestForkTitle:
    def test_an_unnumbered_title_starts_at_two(self) -> None:
        assert fork_title("The River", set()) == "The River - 2"

    def test_a_numbered_title_counts_up_from_its_own_number(self) -> None:
        # Not "the first free from 2": a fork must never be numbered
        # below the story it was made from, however many gaps stand open.
        assert fork_title("The River - 5", {"The River - 2", "The River - 3"}) == "The River - 6"

    def test_the_numbering_is_replaced_never_appended(self) -> None:
        assert fork_title("The River - 3", set()) == "The River - 4"

    def test_a_pile_of_numbering_collapses_to_the_stem(self) -> None:
        assert fork_title("The River - 3 - 3 - 2", set()) == "The River - 3"

    def test_a_taken_name_is_skipped(self) -> None:
        taken = {"The River - 2", "The River - 3", "The River - 4"}
        assert fork_title("The River", taken) == "The River - 5"

    def test_only_a_trailing_number_counts_as_numbering(self) -> None:
        assert fork_title("Chapter 7", set()) == "Chapter 7 - 2"
        assert fork_title("Book 2 of 3", set()) == "Book 2 of 3 - 2"

    def test_a_title_that_is_only_a_number_keeps_itself(self) -> None:
        assert fork_title(" - 2", set()) == " - 2 - 2"

    def test_a_non_latin_title_numbers_like_any_other(self) -> None:
        assert fork_title("Река — 2", set()) == "Река — 2 - 2"  # an em dash is not the separator
        assert fork_title("Река - 2", set()) == "Река - 3"

    def test_an_untitled_source_forks_untitled(self) -> None:
        assert fork_title("", set()) is None
