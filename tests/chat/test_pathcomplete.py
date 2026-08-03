"""Path completion — the pure core.

The contract: `split` divides a partly typed path into the directory part
(everything through the final `/`, kept exactly as typed — `~` stays
unexpanded, spaces survive) and the fragment being completed after it.
`matches` picks the display names among (name, is_dir) entries that
complete the fragment: prefix-matched, hidden entries offered only when
the fragment itself starts with a dot, directories shown with a trailing
`/`, ordered by casefolded name.
"""

from otaku.chat.pathcomplete import matches, split


class TestSplit:
    def test_no_separator_means_all_fragment(self) -> None:
        assert split("sto") == ("", "sto")

    def test_divides_at_the_final_separator(self) -> None:
        assert split("tales/winter/ch") == ("tales/winter/", "ch")

    def test_a_trailing_separator_leaves_an_empty_fragment(self) -> None:
        assert split("tales/") == ("tales/", "")

    def test_the_home_shorthand_stays_as_typed(self) -> None:
        assert split("~/stories/dr") == ("~/stories/", "dr")

    def test_spaces_survive_in_both_parts(self) -> None:
        assert split("my tales/first ch") == ("my tales/", "first ch")

    def test_empty_input_is_two_empties(self) -> None:
        assert split("") == ("", "")


class TestMatches:
    ENTRIES = (
        ("notes.txt", False),
        ("Tales", True),
        ("archive", True),
        (".hidden", False),
        ("night.md", False),
    )

    def test_prefix_filters_and_dirs_get_a_slash(self) -> None:
        assert matches(self.ENTRIES, "n") == ["night.md", "notes.txt"]
        assert matches(self.ENTRIES, "Tal") == ["Tales/"]

    def test_an_empty_fragment_offers_everything_visible(self) -> None:
        assert matches(self.ENTRIES, "") == ["archive/", "night.md", "notes.txt", "Tales/"]

    def test_hidden_entries_need_a_dotted_fragment(self) -> None:
        assert matches(self.ENTRIES, ".") == [".hidden"]
        assert ".hidden" not in matches(self.ENTRIES, "")

    def test_ordering_is_casefolded(self) -> None:
        names = matches([("beta", False), ("Alpha", False), ("gamma", True)], "")
        assert names == ["Alpha", "beta", "gamma/"]

    def test_nothing_matching_is_an_empty_list(self) -> None:
        assert matches(self.ENTRIES, "zzz") == []
