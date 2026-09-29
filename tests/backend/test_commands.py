"""The shared command surface: what a spec tells the frontends about a
row, and how a typed line is read against the table.

`find` binds a typed line's head to its spec, the longest match winning
for the /set family; the inliner rows never match a line's start — they
close a line, they do not open one. `raw_argument` cuts the argument the
one way both frontends must cut it, and `unknown_notice` is the sentence
both show for a line no row answers. What a row's `args` shape means for a
MENU — whether accepting it leaves the line open — is the frontend's
rule, held beside the menu that acts on it.
"""

from otaku.backend.commands import (
    COMMANDS,
    GROUP_LABELS,
    CommandType,
    find,
    raw_argument,
    unknown_notice,
)


class TestFind:
    def test_a_bare_token_finds_its_row(self) -> None:
        spec = find("/fork my title")
        assert spec is not None and spec.token == "/fork"

    def test_the_longest_match_wins_for_the_set_family(self) -> None:
        spec = find("/set think medium")
        assert spec is not None and spec.token == "/set think"

    def test_a_bare_set_is_unknown_notice(self) -> None:
        # The family's usage line is composed by the frontend FROM the
        # table; the bare word names no row.
        assert find("/set") is None

    def test_an_unknown_word_finds_nothing(self) -> None:
        assert find("/nonesuch") is None

    def test_a_direction_finds_its_syntax_row(self) -> None:
        spec = find("/me Elara: hi")
        assert spec is not None and spec.kind is CommandType.SYNTAX

    def test_an_inliner_row_never_matches_a_line_start(self) -> None:
        # `/cue` exists only behind the `…` of the inline group.
        assert find("/cue whisper") is None


class TestRawArgument:
    """The one splitting rule both frontends' Python reads a line by:
    everything after the token, verbatim from the first non-space
    character."""

    def test_a_one_word_token_leaves_the_rest(self) -> None:
        assert raw_argument("/title The River", "/title") == "The River"

    def test_a_family_token_takes_both_words(self) -> None:
        assert raw_argument("/set think medium", "/set think") == "medium"

    def test_untidy_spacing_still_finds_the_argument(self) -> None:
        # The reason this is not a fixed-width slice: that would hand an
        # operation "k  medium".
        assert raw_argument("/set  think  medium", "/set think") == "medium"

    def test_spacing_inside_the_argument_is_kept(self) -> None:
        # Verbatim from the first non-space character — a story's title
        # is the reader's text, not the parser's.
        assert raw_argument("/title  A  B", "/title") == "A  B"

    def test_a_bare_command_has_no_argument(self) -> None:
        assert raw_argument("/fork", "/fork") == ""

    def test_trailing_space_is_no_argument(self) -> None:
        assert raw_argument("/fork   ", "/fork") == ""


class TestUnknownNotice:
    """The sentence for a line no row answers — one home, shown by both
    frontends verbatim."""

    def test_an_unknown_word_names_itself_and_the_way_out(self) -> None:
        sentence = unknown_notice("/frobnicate now")
        assert "/frobnicate" in sentence
        assert "/help" in sentence

    def test_any_string_gets_a_sentence(self) -> None:
        # Total by contract: the web hands it whatever arrived on the
        # socket, an empty body included.
        assert unknown_notice("")
        assert unknown_notice("   ")

    def test_the_set_family_answers_with_its_usage_line(self) -> None:
        # Composed FROM the table, so the usage line and the commands
        # can never disagree — every /set row is in it.
        sentence = unknown_notice("/set bogus on")
        assert sentence.startswith("Usage: ")
        for spec in COMMANDS:
            if spec.token.startswith("/set "):
                assert spec.token in sentence


class TestTable:
    def test_every_token_is_unique_per_surface(self) -> None:
        tokens = [spec.token for spec in COMMANDS]
        assert len(tokens) == len(set(tokens))

    def test_every_row_names_a_group_and_a_kind(self) -> None:
        for spec in COMMANDS:
            assert spec.group and isinstance(spec.kind, CommandType), spec.token

    def test_the_inliners_live_behind_the_ellipsis(self) -> None:
        # Written in the table behind a `…`, which is how the mid-line
        # menu looks them up without colliding with the line-start /ooc.
        inliners = [spec for spec in COMMANDS if spec.token.startswith("… ")]
        assert {spec.token for spec in inliners} == {"… /ooc", "… /cue"}

    def test_every_group_has_a_label(self) -> None:
        # Both frontends draw their own help page from the table, and
        # each looks its groups up here — a group with no entry would
        # raise in one and print nothing in the other.
        assert {spec.group for spec in COMMANDS} <= set(GROUP_LABELS)
