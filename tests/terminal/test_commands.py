"""The terminal's command table: what a spec tells the terminal about a
row — its token unique, its group labelled, its kind declared, the
inliners filed where the mid-line menu looks them up — and `is_command`,
what makes a typed line the terminal's: a slash word that opens nothing
in the story's language."""

from otaku.terminal.commands import COMMANDS, GROUP_LABELS, CommandType, is_command


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
        # The help page looks each row's group up here: a group with no
        # entry would fail it.
        assert {spec.group for spec in COMMANDS} <= set(GROUP_LABELS)


class TestIsCommand:
    def test_a_slash_word_that_is_not_a_direction_is_a_command(self) -> None:
        assert is_command("/fork my title") is True
        assert is_command("/nonsense too") is True

    def test_a_direction_is_story_not_a_command(self) -> None:
        assert is_command("/me Elara: hi") is False
        assert is_command("/you Elara") is False
        assert is_command("/ooc what?") is False
        assert is_command("/roll 1d20+5") is False

    def test_prose_is_never_a_command(self) -> None:
        assert is_command("She looks up.") is False
        assert is_command("and/or") is False
