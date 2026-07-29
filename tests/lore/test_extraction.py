"""The extraction's pure pieces: span packing and the numbered scene.

`pack`'s contract: every span meets BOTH minimums (characters and item
count), and a leftover under the minimums merges into the span before it.
`numbered_chat`'s: the analysis model sees `[n]` numbering, an attributed
line's speaker, composed framing, and an `((OOC: …))` enclosure on every
out-of-character row — added when the row has no stored framing to show
one.
"""

from otaku.lore.extraction import numbered_chat, pack
from otaku.store.schema import Message


class TestPack:
    def test_cuts_when_both_minimums_are_met(self) -> None:
        assert pack([10] * 10, min_chars=25, min_messages=2) == [(0, 3), (3, 6), (6, 10)]

    def test_char_minimum_alone_does_not_cut(self) -> None:
        # Two huge items: chars satisfied instantly, count holds the cut.
        assert pack([1000, 1000, 1000], min_chars=100, min_messages=3) == [(0, 3)]

    def test_message_minimum_alone_does_not_cut(self) -> None:
        # Many tiny items: count satisfied, chars hold the cut.
        assert pack([1] * 6, min_chars=100, min_messages=2) == [(0, 6)]

    def test_a_leftover_under_the_minimums_merges_into_the_last_span(self) -> None:
        assert pack([10] * 5, min_chars=20, min_messages=2) == [(0, 2), (2, 5)]

    def test_a_tail_under_the_minimums_is_still_one_span(self) -> None:
        assert pack([5, 5], min_chars=1000, min_messages=10) == [(0, 2)]

    def test_spans_cover_everything_in_order(self) -> None:
        spans = pack([100, 3, 200, 4, 5, 300], min_chars=150, min_messages=2)
        flat = [i for a, b in spans for i in range(a, b)]
        assert flat == list(range(6))

    def test_empty_input_packs_to_nothing(self) -> None:
        assert pack([], min_chars=10, min_messages=2) == []


class TestNumberedChat:
    def test_numbers_each_message(self) -> None:
        text = numbered_chat(
            [Message(role="user", body="First."), Message(role="assistant", body="Second.")]
        )
        assert text == "[1] First.\n[2] Second."

    def test_an_attributed_line_carries_its_speaker(self) -> None:
        text = numbered_chat([Message(role="user", body="I wait.", speaker="Ryn")])
        assert text == "[1] Ryn: I wait."

    def test_framing_is_composed_onto_the_body(self) -> None:
        message = Message(role="user", body="I wait.", framing="((OOC: as Ryn.))\n{body}")
        assert numbered_chat([message]) == "[1] ((OOC: as Ryn.))\nI wait."

    def test_a_bare_ooc_row_gains_the_enclosure(self) -> None:
        message = Message(role="assistant", body="Good plan.", kind="ooc", framing=None)
        assert numbered_chat([message]) == "[1] ((OOC: Good plan.))"

    def test_an_ooc_row_with_framing_shows_it_as_stored(self) -> None:
        message = Message(role="user", body="Plan?", kind="ooc", framing="((OOC: {body}))")
        assert numbered_chat([message]) == "[1] ((OOC: Plan?))"
