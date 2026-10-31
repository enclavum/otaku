"""Where an injection goes, tested from the module's contract.

`system_injections` are those at the system position, in the order
given, an empty one not sent. `inject_into_tail` places each numbered
injection itself before that one of the reader's messages counted from
the end — 1 the newest, 2 the previous; a reply, a tool's row, a recap
row or another injection is never counted — never above the wall
(where the recap ends) and never after the newest row; injections
sharing a place keep the order given, and an empty one is not sent. A
system injection is the system message's, never placed. Only a story
with no rows at all sends one on its own.
"""

from otaku.context.injections import Injection, inject_into_tail, system_injections
from otaku.store.schema import InjectionPosition, Message

ONE = Injection("one", "One.", InjectionPosition(1))
TWO = Injection("two", "Two.", InjectionPosition(2))
TOP = Injection("top", "Top.", InjectionPosition())


class TestIntoTheSystemMessage:
    def test_only_those_at_the_system_position_go_in_it(self) -> None:
        assert system_injections([TOP, ONE, TWO]) == [TOP]

    def test_they_keep_the_order_given(self) -> None:
        other = Injection("other", "Other.", InjectionPosition())
        assert system_injections([TOP, ONE, other]) == [TOP, other]

    def test_an_empty_one_is_not_sent(self) -> None:
        assert system_injections([Injection("x", "", InjectionPosition())]) == []


class TestIntoTheTail:
    def test_one_rides_before_the_newest_row(self) -> None:
        assert bodies(inject_into_tail(rows(3), [ONE], 0)) == ["r1", "r2", "One.", "r3"]

    def test_a_position_counts_the_readers_rows_from_the_end(self) -> None:
        # r5, r3 and r1 are the reader's; the replies between are not counted.
        assert bodies(inject_into_tail(rows(5), [TWO], 0)) == [
            "r1",
            "r2",
            "Two.",
            "r3",
            "r4",
            "r5",
        ]
        third = Injection("third", "Third.", InjectionPosition(3))
        assert bodies(inject_into_tail(rows(5), [third], 0))[:2] == ["Third.", "r1"]

    def test_the_newest_row_keeps_the_last_word_even_when_it_is_a_reply(self) -> None:
        # r2 is a reply: the reader's newest is r1, and the reply stays last
        assert bodies(inject_into_tail(rows(2), [ONE], 0)) == ["One.", "r1", "r2"]

    def test_neither_a_tools_row_nor_another_injection_is_counted(self) -> None:
        tool = Message(role="tool", kind="tool", body="t", id=9)
        given = [*rows(1), tool, Message(role="user", body="r2", id=2)]
        assert bodies(inject_into_tail(given, [TWO], 0)) == ["Two.", "r1", "t", "r2"]
        assert bodies(inject_into_tail(rows(3), [ONE, TWO], 0)) == [
            "Two.",
            "r1",
            "r2",
            "One.",
            "r3",
        ]

    def test_a_position_past_the_top_stops_at_the_top(self) -> None:
        assert bodies(
            inject_into_tail(rows(2), [Injection("x", "X.", InjectionPosition(8))], 0)
        ) == [
            "X.",
            "r1",
            "r2",
        ]

    def test_the_wall_is_never_crossed(self) -> None:
        # The recap ends at row 2: an injection asked above it stays under it.
        placed = inject_into_tail(rows(4), [Injection("x", "X.", InjectionPosition(8))], 2)
        assert bodies(placed) == ["r1", "r2", "X.", "r3", "r4"]

    def test_a_readers_row_above_the_wall_is_never_counted(self) -> None:
        # r1 is above it: the third of the reader's rows is never reached
        third = Injection("x", "X.", InjectionPosition(3))
        assert bodies(inject_into_tail(rows(5), [third], 2)) == ["r1", "r2", "X.", "r3", "r4", "r5"]

    def test_the_same_place_keeps_the_order_given(self) -> None:
        other = Injection("other", "Other.", InjectionPosition(1))
        assert bodies(inject_into_tail(rows(1), [ONE, other], 0)) == ["One.", "Other.", "r1"]

    def test_the_order_given_holds_whatever_depth_led_to_the_place(self) -> None:
        deep = Injection("x", "X.", InjectionPosition(8))
        assert bodies(inject_into_tail(rows(1), [deep, ONE], 0)) == ["X.", "One.", "r1"]
        assert bodies(inject_into_tail(rows(1), [ONE, deep], 0)) == ["One.", "X.", "r1"]

    def test_a_system_injection_and_an_empty_one_are_not_placed(self) -> None:
        placed = inject_into_tail(rows(2), [TOP, Injection("x", "", InjectionPosition(1))], 0)
        assert bodies(placed) == ["r1", "r2"]

    def test_a_story_with_no_rows_sends_the_injection_alone(self) -> None:
        assert bodies(inject_into_tail([], [ONE], 0)) == ["One."]

    def test_an_injection_is_placed_itself(self) -> None:
        placed = [r for r in inject_into_tail(rows(1), [ONE], 0) if isinstance(r, Injection)]
        assert placed == [ONE]

    def test_the_rows_given_are_untouched(self) -> None:
        given = rows(3)
        inject_into_tail(given, [ONE, TWO], 0)
        assert bodies(given) == ["r1", "r2", "r3"]


def rows(n: int) -> list[Message]:
    return [
        Message(role="user" if i % 2 else "assistant", body=f"r{i}", id=i) for i in range(1, n + 1)
    ]


def bodies(placed: list[Message | Injection]) -> list[str]:
    return [row.text if isinstance(row, Injection) else row.body for row in placed]
