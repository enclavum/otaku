"""The cursor simulation — the pure surface of terminal/cursor.py.

The contract: `RowTracker` simulates the cursor of a VT100-family terminal
consuming otaku's own output, and `rows` is the number of completed row
advances — the rows above the one the cursor is on. Wrapping happens at the
fixed width with the real terminal's deferred wrap (filling the last column
advances nothing until the next printable character), wide characters take
two columns, `\r` `\t` `\b` move without advancing, and escape sequences —
CSI, OSC, and two-byte ESC forms — occupy nothing, even split across feeds.
`measure` runs a fresh tracker over one string; a printed line or block is
measured with its trailing newline. (Asking the real terminal lives in
`terminal.query`, exercised by the pty scenarios.)
"""

from otaku.terminal import user_block
from otaku.terminal.cursor import RowTracker, measure


class TestRowTracker:
    def test_short_text_stays_on_one_row(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("hello")
        assert tracker.rows == 0
        assert tracker.column == 5

    def test_a_newline_advances_one_row(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("hello\n")
        assert tracker.rows == 1
        assert tracker.column == 0

    def test_long_text_wraps_at_the_width(self) -> None:
        tracker = RowTracker(10)
        tracker.feed("a" * 25)
        assert tracker.rows == 2
        assert tracker.column == 5

    def test_a_line_exactly_the_width_defers_its_wrap(self) -> None:
        # The terminal keeps the cursor on the filled row; a newline there
        # advances once, not twice.
        tracker = RowTracker(10)
        tracker.feed("a" * 10)
        assert tracker.rows == 0
        tracker.feed("\n")
        assert tracker.rows == 1
        assert tracker.column == 0

    def test_the_deferred_wrap_lands_before_the_next_character(self) -> None:
        tracker = RowTracker(10)
        tracker.feed("a" * 10 + "b")
        assert tracker.rows == 1
        assert tracker.column == 1

    def test_carriage_return_rewinds_the_column(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("abc\rx")
        assert tracker.rows == 0
        assert tracker.column == 1

    def test_carriage_return_clears_a_pending_wrap(self) -> None:
        tracker = RowTracker(4)
        tracker.feed("abcd\rX")
        assert tracker.rows == 0
        assert tracker.column == 1

    def test_wide_characters_take_two_columns(self) -> None:
        tracker = RowTracker(10)
        tracker.feed("ああ")
        assert tracker.rows == 0
        assert tracker.column == 4

    def test_a_wide_character_wraps_when_one_column_remains(self) -> None:
        tracker = RowTracker(5)
        tracker.feed("abcdあ")
        assert tracker.rows == 1
        assert tracker.column == 2

    def test_zero_width_marks_occupy_nothing(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("é")
        assert tracker.column == 1

    def test_tab_advances_to_the_next_stop(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("ab\tX")
        assert tracker.rows == 0
        assert tracker.column == 9

    def test_tab_never_passes_the_last_column(self) -> None:
        tracker = RowTracker(10)
        tracker.feed("a" * 9 + "\t")
        assert tracker.rows == 0
        assert tracker.column == 9

    def test_backspace_steps_back_but_not_past_zero(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("abc\b")
        assert tracker.column == 2
        tracker.feed("\b\b\b\b")
        assert tracker.column == 0

    def test_sgr_sequences_are_invisible(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("\x1b[1mA\x1b[0m")
        assert tracker.rows == 0
        assert tracker.column == 1

    def test_erase_and_motion_sequences_are_invisible(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("\x1b[2K\x1b[1A\x1b[J")
        assert tracker.rows == 0
        assert tracker.column == 0

    def test_an_escape_split_across_feeds_stays_invisible(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("\x1b[")
        tracker.feed("48;2;240;240;240m")
        tracker.feed("x")
        assert tracker.column == 1

    def test_osc_sequences_are_invisible(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("\x1b]0;title\x07A")
        assert tracker.column == 1

    def test_two_byte_escapes_are_invisible(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("\x1b7A\x1b8")
        assert tracker.column == 1

    def test_other_control_characters_occupy_nothing(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("\x07a\x00")
        assert tracker.column == 1

    def test_reset_forgets_everything(self) -> None:
        tracker = RowTracker(80)
        tracker.feed("abc\ndef")
        tracker.reset()
        assert tracker.rows == 0
        assert tracker.column == 0

    def test_width_is_clamped_to_one(self) -> None:
        tracker = RowTracker(0)
        tracker.feed("ab")
        assert tracker.rows == 1


class TestMeasure:
    def test_measures_a_printed_line(self) -> None:
        assert measure("hello\n", 80) == 1

    def test_measures_a_wrapped_block(self) -> None:
        assert measure("a" * 25 + "\n", 10) == 3

    def test_a_full_width_line_measures_one_row(self) -> None:
        assert measure("a" * 10 + "\n", 10) == 1

    def test_measures_the_user_block_by_its_visible_text(self) -> None:
        assert measure(user_block("hi") + "\n", 80) == 1
        assert measure(user_block("x" * 100) + "\n", 80) == 2

    def test_measures_a_multiline_block(self) -> None:
        assert measure(user_block("one\ntwo") + "\n", 80) == 2
