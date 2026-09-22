"""Where an injection goes, tested from the module's contract.

`inject_into_system` appends every "system" injection after the premise,
a blank line between, and is the injections alone without one; a
numbered injection is not its business. `inject_into_tail` gives each
numbered injection a row of its own, placed as `list.insert` would place
it: -1 above the newest row, -3 above the last three, never above the
wall (where the recap ends) and never after the newest row; a number
that is not negative rides as -1, injections sharing a place keep the
order given, and an empty one is not sent. Only a story with no rows at
all sends one on its own.
"""

from otaku.context.injections import ROW_KIND, Injection, inject_into_system, inject_into_tail
from otaku.store.schema import Message

ONE = Injection("one", "One.", -1)
THREE = Injection("three", "Three.", -3)
TOP = Injection("top", "Top.", "system")


class TestIntoTheSystemMessage:
    def test_a_system_injection_follows_the_premise(self) -> None:
        assert inject_into_system("Premise.", [TOP]) == "Premise.\n\nTop."

    def test_without_a_premise_the_injections_are_the_message(self) -> None:
        assert inject_into_system("", [TOP]) == "Top."

    def test_a_numbered_injection_is_not_its_business(self) -> None:
        assert inject_into_system("Premise.", [ONE, THREE]) == "Premise."

    def test_an_empty_one_is_not_sent(self) -> None:
        assert inject_into_system("Premise.", [Injection("x", "", "system")]) == "Premise."

    def test_several_keep_the_order_given(self) -> None:
        second = Injection("second", "Second.", "system")
        assert inject_into_system("", [TOP, second]) == "Top.\n\nSecond."


class TestIntoTheTail:
    def test_minus_one_rides_above_the_newest_row(self) -> None:
        assert bodies(inject_into_tail(rows(3), [ONE], 0)) == ["r1", "r2", "One.", "r3"]

    def test_a_position_counts_rows_from_the_end(self) -> None:
        assert bodies(inject_into_tail(rows(5), [THREE], 0)) == [
            "r1",
            "r2",
            "Three.",
            "r3",
            "r4",
            "r5",
        ]

    def test_the_newest_row_always_keeps_the_last_word(self) -> None:
        for position in (0, 2):
            placed = inject_into_tail(rows(3), [Injection("x", "X.", position)], 0)
            assert bodies(placed) == ["r1", "r2", "X.", "r3"]

    def test_a_position_past_the_top_stops_at_the_top(self) -> None:
        assert bodies(inject_into_tail(rows(2), [Injection("x", "X.", -8)], 0)) == [
            "X.",
            "r1",
            "r2",
        ]

    def test_the_wall_is_never_crossed(self) -> None:
        # The recap ends at row 2: an injection asked above it stays under it.
        placed = inject_into_tail(rows(4), [Injection("x", "X.", -8)], 2)
        assert bodies(placed) == ["r1", "r2", "X.", "r3", "r4"]

    def test_the_same_place_keeps_the_order_given(self) -> None:
        other = Injection("other", "Other.", -1)
        assert bodies(inject_into_tail(rows(1), [ONE, other], 0)) == ["One.", "Other.", "r1"]

    def test_a_system_injection_and_an_empty_one_are_not_placed(self) -> None:
        placed = inject_into_tail(rows(2), [TOP, Injection("x", "", -1)], 0)
        assert bodies(placed) == ["r1", "r2"]

    def test_a_story_with_no_rows_sends_the_injection_alone(self) -> None:
        assert bodies(inject_into_tail([], [ONE], 0)) == ["One."]

    def test_an_injected_row_is_a_wire_only_user_row(self) -> None:
        (row,) = [r for r in inject_into_tail(rows(1), [ONE], 0) if r.kind == ROW_KIND]
        assert (row.role, row.body, row.id) == ("user", "One.", 0)

    def test_the_rows_given_are_untouched(self) -> None:
        given = rows(3)
        inject_into_tail(given, [ONE, THREE], 0)
        assert bodies(given) == ["r1", "r2", "r3"]


def rows(n: int) -> list[Message]:
    return [Message("user" if i % 2 else "assistant", f"r{i}", id=i) for i in range(1, n + 1)]


def bodies(placed: list[Message]) -> list[str]:
    return [row.body for row in placed]
