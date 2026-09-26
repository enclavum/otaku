"""The launch's pure rules: the shipped layout of a sample's pictures.

A sample's pictures ship beside its document, named
`<stem>.<message>.<order>.<ext>`: `_sample_pictures` picks out the ones
for a stem among a directory's names, each with the message number it
belongs to, in message and then picture order; anything else in the
directory — the documents, another sample's pictures, a name that does
not follow the rule — is left alone.
"""

from otaku.backend.launch import _sample_pictures


class TestSamplePictures:
    def test_a_samples_pictures_are_named_for_their_message_and_ordered(self) -> None:
        names = ["river.md", "river.15.2.jpg", "river.15.1.png", "river.3.1.webp", "tour.md"]
        assert _sample_pictures(names, "river") == [
            (3, "river.3.1.webp"),
            (15, "river.15.1.png"),
            (15, "river.15.2.jpg"),
        ]

    def test_another_samples_pictures_are_not_this_ones(self) -> None:
        assert _sample_pictures(["tour.15.1.jpg", "river.md"], "river") == []

    def test_a_name_off_the_rule_is_left_alone(self) -> None:
        names = ["river.15.jpg", "river.a.1.jpg", "river.15.1", "river-15-1.jpg", "river.15.1.jpg"]
        assert _sample_pictures(names, "river") == [(15, "river.15.1.jpg")]

    def test_the_order_is_numeric_not_lexical(self) -> None:
        names = ["river.15.10.jpg", "river.15.2.jpg", "river.15.1.jpg"]
        assert [n for _, n in _sample_pictures(names, "river")] == [
            "river.15.1.jpg",
            "river.15.2.jpg",
            "river.15.10.jpg",
        ]
