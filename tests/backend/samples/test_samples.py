"""The samples' pure rules: the shipped layout of a sample's pictures,
and what a card's manifest row sets on its card.

A sample's pictures ship in the samples' images folder, named
`<stem>.<message>.<order>.<ext>`: `_sample_pictures` picks out the ones
for a stem among the folder's names, each with the message number it
belongs to, in message and then picture order; anything else there —
another sample's pictures, a card's portrait, a name that does not
follow the rule — is left alone. A manifest row's fields
(`_card_fields`) are the ones it gives, each as the card's update takes
it; one it leaves out keeps what the story's character brought.
"""

from otaku.backend.samples import _card_fields, _sample_pictures


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


class TestCardFields:
    def test_a_rows_fields_as_the_cards_update_takes_them(self) -> None:
        row = {
            "story": "river",
            "name": "Maren",
            "picture": "maren.jpg",
            "tagline": "Maps",
            "description": "A cartographer.",
            "examples": ["{{user}}: Hi.\n{{char}}: Hello.", "{{user}}: Bye."],
        }
        assert _card_fields(row) == {
            "tagline": "Maps",
            "description": "A cartographer.",
            "examples": ("{{user}}: Hi.\n{{char}}: Hello.", "{{user}}: Bye."),
        }

    def test_a_field_left_out_or_left_empty_is_not_set(self) -> None:
        assert (
            _card_fields({"story": "river", "name": "Maren", "greeting": "", "examples": []}) == {}
        )
