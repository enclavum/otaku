"""The screen ledger's account of where the text ends.

`note_printed` keeps the text's last line as printed, and the newlines
since: text extends the line, a newline ends it, and text after newlines
starts the next — but newlines alone, and escapes alone (a style's reset
written after a reply's last newline), start no line. A reply that stops
mid-row in story mode leaves the row open (`hold_at_tail`): the cursor
stands where the text ends, and stepping back up onto the text's last
line from there climbs only that line's own rows — none for a line of
one row, never `ESC[0A`, which a terminal reads as one.
"""

import pytest

from otaku.terminal.chat.ledger import ScreenLedger

RESET = "\x1b[0m"


class TestNotePrinted:
    def test_text_extends_the_last_line(self) -> None:
        ledger = ScreenLedger()
        ledger.note_printed("She waited")
        ledger.note_printed(" for the bus.")
        assert ledger.tail == "She waited for the bus."

    def test_a_newline_ends_the_line_and_text_after_it_starts_the_next(self) -> None:
        ledger = ScreenLedger()
        ledger.note_printed("One.\nTwo")
        assert ledger.tail == "Two"
        ledger.note_printed("\n\nThree.")
        assert ledger.tail == "Three."

    def test_newlines_alone_keep_the_texts_line(self) -> None:
        ledger = ScreenLedger()
        ledger.note_printed("The end.")
        ledger.note_printed("\n\n")
        assert ledger.tail == "The end."

    def test_escapes_alone_start_no_line(self) -> None:
        ledger = ScreenLedger()
        ledger.note_printed(f"- Try. Short reply.{RESET}\n{RESET}")
        assert ledger.tail == f"- Try. Short reply.{RESET}"
        # held for the next line, should one start
        ledger.note_printed("Next.")
        assert ledger.tail == f"{RESET}Next."


class TestHoldAtTail:
    def test_a_held_row_stands_at_the_texts_end(self) -> None:
        ledger = ScreenLedger()
        ledger.note_printed("She waited.")
        ledger.hold_at_tail()
        assert ledger.at_text_end

    def test_a_line_of_one_row_is_climbed_by_no_move(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ledger = ScreenLedger()
        ledger.note_printed("She waited.")
        ledger.hold_at_tail()
        ledger.lift_to_tail()
        written = capsys.readouterr().out
        assert "A" not in written.replace("\x1b[?25l", "")
