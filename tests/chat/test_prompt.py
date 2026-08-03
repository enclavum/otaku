"""The prompt's pure pieces: the shortcut carry and the multiline assembler.

`Carry` hands a shortcut's exit across the prompt boundary: `take_text`
consumes the restored draft (once), while `text` itself stays readable
for the erase of what was shown; `take_shortcut` is a one-shot flag.

`LineAssembler`'s contract is the `\"\"\"` convention: a line starting the
delimiter opens a block that collects lines until one ends with it; the
text between the delimiters — newlines preserved — comes back as a single
raw message that must never be treated as a command. A plain line passes
straight through.
"""

from otaku.chat.prompt import Carry, LineAssembler


class TestCarry:
    def test_starts_empty(self) -> None:
        carry = Carry()
        assert carry.take_text() == ""
        assert carry.take_shortcut() is False

    def test_take_text_returns_once_and_clears(self) -> None:
        carry = Carry()
        carry.text = "half-typed line"
        assert carry.take_text() == "half-typed line"
        assert carry.take_text() == ""

    def test_take_shortcut_is_one_shot(self) -> None:
        carry = Carry()
        carry.shortcut = True
        assert carry.take_shortcut() is True
        assert carry.take_shortcut() is False

    def test_taking_the_shortcut_leaves_the_text_readable(self) -> None:
        # The run loop erases what was SHOWN (carry.text) after consuming
        # the shortcut flag; only the next prompt's default consumes it.
        carry = Carry()
        carry.text = "draft"
        carry.shortcut = True
        assert carry.take_shortcut() is True
        assert carry.text == "draft"


class TestLineAssembler:
    def test_a_plain_line_passes_through(self) -> None:
        assert LineAssembler().feed("hello") == ("hello", False)

    def test_a_slash_line_passes_through_unmarked(self) -> None:
        assert LineAssembler().feed("/undo") == ("/undo", False)

    def test_a_block_collects_until_the_closing_delimiter(self) -> None:
        assembler = LineAssembler()
        assert assembler.feed('"""first') is None
        assert assembler.feed("second") is None
        assert assembler.feed('third"""') == ("first\nsecond\nthird", True)

    def test_newlines_survive_inside_a_block(self) -> None:
        assembler = LineAssembler()
        assembler.feed('"""a')
        assembler.feed("")
        assert assembler.feed('b"""') == ("a\n\nb", True)

    def test_a_single_line_block_closes_immediately(self) -> None:
        assert LineAssembler().feed('"""text"""') == ("text", True)

    def test_block_text_is_raw_even_when_it_looks_like_a_command(self) -> None:
        text, is_raw = LineAssembler().feed('"""/regen"""')
        assert text == "/regen"
        assert is_raw is True

    def test_in_block_reports_the_open_state(self) -> None:
        assembler = LineAssembler()
        assert assembler.in_block is False
        assembler.feed('"""open')
        assert assembler.in_block is True

    def test_reset_drops_a_partial_block(self) -> None:
        assembler = LineAssembler()
        assembler.feed('"""dropped')
        assembler.reset()
        assert assembler.in_block is False
        assert assembler.feed("fresh") == ("fresh", False)
