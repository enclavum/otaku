"""The model picker's pure row arithmetic: where the cursor's visual row
falls in the list `_items_text` draws — a caption and a blank above each
provider's group, a blank between groups — and what a cursor past the
rows or an empty list yields, since the control must find the row inside
the text drawn."""

from dataclasses import dataclass

from otaku.terminal.screens.models.base import cursor_line


@dataclass(frozen=True)
class Row:
    provider_name: str


class TestCursorLine:
    def test_the_first_row_sits_under_its_caption_and_blank(self) -> None:
        assert cursor_line([Row("a")], 0) == 2

    def test_rows_of_one_group_follow_one_per_line(self) -> None:
        rows = [Row("a"), Row("a"), Row("a")]
        assert [cursor_line(rows, i) for i in range(3)] == [2, 3, 4]

    def test_a_new_group_costs_a_gap_a_caption_and_a_blank(self) -> None:
        rows = [Row("a"), Row("a"), Row("b"), Row("b")]
        assert cursor_line(rows, 2) == 2 + 2 + 3
        assert cursor_line(rows, 3) == 2 + 2 + 3 + 1

    def test_a_cursor_past_the_rows_lands_on_the_last_row(self) -> None:
        rows = [Row("a"), Row("b")]
        assert cursor_line(rows, 5) == cursor_line(rows, 1)

    def test_an_empty_list_is_row_zero(self) -> None:
        assert cursor_line([], 0) == 0
        assert cursor_line([], 3) == 0
