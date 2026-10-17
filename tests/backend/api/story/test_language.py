"""The story's typed language as a client is handed it
(`backend.api.story.language`): the words a line may open with and the
ones it may carry inside, each a token and the shape of its argument,
the two halves disjoint so a menu offers the right one at the caret —
commands no part of it — and the sentence for a line none of it frames.
"""

from otaku.backend.api.story.language import INLINERS, OPENERS, PROSE_DESCRIPTION, opener


class TestRows:
    def test_every_row_is_a_slash_word_and_its_argument_shape(self) -> None:
        for row in OPENERS + INLINERS:
            assert row.token.startswith(("/", "…")), row
            assert isinstance(row.args, str), row

    def test_the_two_halves_are_disjoint(self) -> None:
        # A word opens a line or rides inside one: the inliners stand
        # behind the `… ` that says so.
        assert OPENERS and INLINERS
        assert all(not row.token.startswith("…") for row in OPENERS)
        assert all(row.token.startswith("… ") for row in INLINERS)

    def test_no_command_is_in_it(self) -> None:
        # Everything typed into the story is story: a command reaching a
        # menu of the language would offer a word no line takes.
        tokens = {row.token for row in OPENERS}
        assert not tokens & {"/stories", "/model", "/set", "/help", "/fork"}

    def test_a_line_with_no_framing_is_explained(self) -> None:
        assert PROSE_DESCRIPTION


class TestOpener:
    def test_a_line_names_the_direction_it_opens_with(self) -> None:
        assert opener("/me Mara: hi") == "/me"
        assert opener("/ooc what now?") == "/ooc"

    def test_prose_and_a_command_open_with_none(self) -> None:
        assert opener("She looks up.") == ""
        assert opener("/fork my title") == ""
