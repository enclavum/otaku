"""The tables' rows beside their columns: a message is written in chat
mode unless it says otherwise, and a card's portraits are its pictures
of the character itself, in order, the first the main one.
"""

from dataclasses import replace

from otaku.store.pictures import CardPicture, CardPictureKind
from otaku.store.schema import Card, Message
from otaku.store.story import StoryMode


class TestMessage:
    def test_a_row_is_chat_unless_it_says_otherwise(self) -> None:
        assert Message(role="user", body="Hi").mode is StoryMode.CHAT
        written = replace(Message(role="user", body="Hi"), mode=StoryMode.STORY)
        assert written.mode is StoryMode.STORY


class TestCard:
    def test_its_portraits_are_its_pictures_of_the_character_in_order(self) -> None:
        main = CardPicture("crd-0012-20261011-0b7e44d1.png", 4, 4, 9)
        joy = CardPicture(
            "crd-0012-20261011-4f2a9c13.png", 4, 4, 9, CardPictureKind.EXPRESSION, "joy"
        )
        side = CardPicture("crd-0012-20261011-91d0e7b4.png", 4, 4, 9)
        inn = CardPicture(
            "crd-0012-20261011-c62069d0.jpg", 4, 4, 9, CardPictureKind.BACKGROUND, "inn"
        )
        card = Card(id=12, name="Seraphina", pictures=(main, joy, side, inn))
        assert card.portraits == (main, side)

    def test_a_card_without_pictures_has_no_portrait(self) -> None:
        assert Card(id=12, name="Seraphina").portraits == ()
