"""What a line is assembled from (`prompt.lines`): the shortcut carry and
the multiline assembler.

`Carry` hands a shortcut's exit across the prompt boundary: `take_text`
consumes the restored draft (once), while `text` itself stays readable
for the erase of what was shown; `take_shortcut` is a one-shot flag.

`LineAssembler`'s contract is the `\"\"\"` convention: a line starting the
delimiter opens a block that collects lines until one ends with it, and
the text between them — newlines preserved — comes back as one message.
A plain line passes straight through, stripped. A block is multiline
entry and nothing more — what it collects is an ordinary prompt, told
apart from a typed line nowhere downstream. The one difference is
settled here: a block keeps the whitespace at its edges, because
someone who opened one to lay text out meant the layout.
"""

from otaku.terminal.prompt.lines import Carry, LineAssembler


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
        assert LineAssembler().feed("hello") == "hello"

    def test_a_plain_line_is_stripped(self) -> None:
        assert LineAssembler().feed("  hello  ") == "hello"

    def test_a_slash_line_passes_through(self) -> None:
        assert LineAssembler().feed("/undo") == "/undo"

    def test_a_block_collects_until_the_closing_delimiter(self) -> None:
        assembler = LineAssembler()
        assert assembler.feed('"""first') is None
        assert assembler.feed("second") is None
        assert assembler.feed('third"""') == "first\nsecond\nthird"

    def test_newlines_survive_inside_a_block(self) -> None:
        assembler = LineAssembler()
        assembler.feed('"""a')
        assembler.feed("")
        assert assembler.feed('b"""') == "a\n\nb"

    def test_a_single_line_block_closes_immediately(self) -> None:
        assert LineAssembler().feed('"""text"""') == "text"

    def test_a_command_may_open_a_block(self) -> None:
        # `/system """` is the obvious way to give a command a multiline
        # argument; the command survives, the delimiters do not.
        assembler = LineAssembler()
        assert assembler.feed('/system """## Premise') is None
        assert assembler.feed("") is None
        assert assembler.feed('a second paragraph"""') == "/system ## Premise\n\na second paragraph"

    def test_a_command_opens_a_one_line_block_too(self) -> None:
        assert LineAssembler().feed('/system """the premise"""') == "/system the premise"

    def test_only_a_real_command_opens_one(self) -> None:
        # Prose that merely mentions the delimiter is prose, and its line
        # has to survive as typed.
        assert LineAssembler().feed('/ooc the marker is """this"""') == (
            '/ooc the marker is """this"""'
        )
        assert LineAssembler().feed('/nonsense """x') == '/nonsense """x'

    def test_a_block_keeps_the_whitespace_at_its_edges(self) -> None:
        # The layout is the reason to open one; stripping it would undo
        # what the writer opened the block to do.
        assert LineAssembler().feed('"""  spaced  """') == "  spaced  "

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
        assert assembler.feed("fresh") == "fresh"
