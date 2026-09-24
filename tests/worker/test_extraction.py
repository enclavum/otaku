"""The extraction's pure pieces: span packing and the numbered scene.

`pack`'s contract: every span meets BOTH minimums (characters and item
count), and a leftover under the minimums merges into the span before it.
`numbered_chat`'s: the analysis model sees `[n]` numbering, an attributed
line's speaker, composed template, and an `((OOC: …))` enclosure on every
out-of-character row — added when the row has no stored template to show
one — and never an `<otk-…>` block.
"""

import pytest

from otaku.store.schema import Attachment, Message
from otaku.worker.extraction import _parse_json, numbered_chat, pack


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
        message = Message(role="user", body="I wait.", template="((OOC: as Ryn.))\n{body}")
        assert numbered_chat([message]) == "[1] ((OOC: as Ryn.))\nI wait."

    def test_a_bare_ooc_row_gains_the_enclosure(self) -> None:
        message = Message(role="assistant", body="Good plan.", kind="ooc", template=None)
        assert numbered_chat([message]) == "[1] ((OOC: Good plan.))"

    def test_an_attributed_framed_row_keeps_its_framing(self) -> None:
        # The speaker decorates the COMPOSED line. Prefixing the body first
        # would hide its leading slash from the composer, sending the row
        # down the legacy path with `{name}` left unfilled.
        message = Message(
            role="user",
            body="/me Elara: I bow.",
            speaker="Elara",
            template="((OOC: as {name}.))\n{body}",
        )
        assert numbered_chat([message]) == "[1] Elara: ((OOC: as Elara.))\nI bow."

    def test_an_ooc_row_with_framing_shows_it_as_stored(self) -> None:
        message = Message(role="user", body="Plan?", kind="ooc", template="((OOC: {body}))")
        assert numbered_chat([message]) == "[1] ((OOC: Plan?))"

    def test_a_block_is_left_out_of_the_numbered_chat(self) -> None:
        # A block is the model's own aside, not the scene: the analysis
        # model never sees it, tags and all, and the numbering stays whole.
        reply = Message(role="assistant", body="She nods.<otk-notes>the letter</otk-notes>")
        text = numbered_chat([Message(role="user", body="I wait."), reply])
        assert text == "[1] I wait.\n[2] She nods."

    def test_a_row_that_is_only_a_block_keeps_its_number(self) -> None:
        # The speaker labels come back BY NUMBER: a row may empty, never vanish.
        span = [
            Message(role="assistant", body="<otk-notes>later</otk-notes>", speaker="Keeper"),
            Message(role="user", body="Go."),
        ]
        assert numbered_chat(span) == "[1] \n[2] Go."

    def test_markup_outside_the_namespace_stays(self) -> None:
        reply = Message(role="assistant", body="She <i>never</i> nods.<plan>x</plan>")
        assert numbered_chat([reply]) == "[1] She <i>never</i> nods.<plan>x</plan>"

    def test_a_picture_is_marked_at_its_line_numbered_through_the_scene(self) -> None:
        # One mark per picture, in the order the request attaches them —
        # so the model can name what it saw by number.
        span = [
            Message(role="user", body="Look.", attachments=(picture("a"),)),
            Message(role="assistant", body="A door."),
            Message(role="user", body="And these.", attachments=(picture("b"), picture("c"))),
        ]
        assert numbered_chat(span).splitlines() == [
            "[1] (picture 1) Look.",
            "[2] A door.",
            "[3] (picture 2) (picture 3) And these.",
        ]

    def test_the_mark_is_a_decoration_the_speaker_still_heads(self) -> None:
        line = Message(role="user", body="Look.", speaker="Ryn", attachments=(picture("a"),))
        assert numbered_chat([line]) == "[1] Ryn: (picture 1) Look."

    def test_a_picture_with_no_words_is_the_mark_alone(self) -> None:
        line = Message(role="user", body="", attachments=(picture("a"),))
        assert numbered_chat([line]) == "[1] (picture 1)"


class TestParseJson:
    """The reply parser's tolerances: fences and prose around the object,
    and JSON syntax typed with typographic double quotes — a small model
    mirrors the story's own punctuation — straightened only on retry, so
    a parsable reply keeps every curly quote inside its values."""

    def test_reads_the_object_out_of_fences_and_prose(self) -> None:
        assert _parse_json('Sure!\n```json\n{"scene": 1}\n```') == {"scene": 1}

    def test_typographic_quotes_as_syntax_parse_on_retry(self) -> None:
        assert _parse_json("{“title”: “Quayside”}") == {"title": "Quayside"}

    def test_a_parsable_reply_keeps_curly_quotes_in_values(self) -> None:
        assert _parse_json('{"line": "She said “hi” — twice"}') == {"line": "She said “hi” — twice"}

    def test_a_reply_without_an_object_refuses(self) -> None:
        with pytest.raises(ValueError):
            _parse_json("no json here")

    def test_a_broken_object_still_refuses(self) -> None:
        with pytest.raises(ValueError):
            _parse_json('{"scene": }')


def picture(name: str) -> Attachment:
    """A picture on a turn, as the column records one."""
    return Attachment(file=f"pic-{name}.jpg", width=10, height=10, size=100)
