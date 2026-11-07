"""A row's pictures as their columns record them. The `attachments`
column's text: a JSON list for a turn with pictures, NULL for one
without — never an empty list pretending to be absent — and the same
rows back. The `pictures` column's: a card's, each with its kind and the
name its card file gave it, a picture recorded before kinds read as a
portrait and a kind this version does not know as another. The keys are
a contract with the SQL that reads the columns (`$.file` is what the
sweep extracts). A picture of one kind is another's file and measure
(`of`).
"""

import json

from otaku.store.pictures import Attachment, CardPicture, CardPictureKind

CAT = Attachment(file="pic-0001-20260918-a3f9c1e2.jpg", width=1568, height=1043, size=312044)
DOG = Attachment(file="pic-0001-20260918-7b02d4ee.png", width=800, height=600, size=90210)
FACE = CardPicture(file="crd-0012-20261011-0b7e44d1.png", width=768, height=1024, size=41200)
JOY = CardPicture(
    file="crd-0012-20261011-4f2a9c13.png",
    width=768,
    height=1024,
    size=39811,
    kind=CardPictureKind.EXPRESSION,
    name="joy",
)


class TestAttachmentsColumn:
    def test_a_turn_without_pictures_is_null(self) -> None:
        assert Attachment.to_json(()) is None

    def test_null_reads_as_no_pictures(self) -> None:
        assert Attachment.from_json(None) == ()
        assert Attachment.from_json("") == ()

    def test_pictures_round_trip_in_order(self) -> None:
        assert Attachment.from_json(Attachment.to_json((CAT, DOG))) == (CAT, DOG)

    def test_the_text_names_the_file_under_the_key_the_sweep_reads(self) -> None:
        (row,) = json.loads(Attachment.to_json((CAT,)) or "")
        assert row["file"] == CAT.file
        assert set(row) == {"file", "width", "height", "size"}


class TestCardPicturesColumn:
    def test_a_card_without_pictures_is_null(self) -> None:
        assert CardPicture.to_json(()) is None
        assert CardPicture.from_json(None) == ()

    def test_pictures_round_trip_in_order_with_their_kind_and_name(self) -> None:
        assert CardPicture.from_json(CardPicture.to_json((FACE, JOY))) == (FACE, JOY)

    def test_the_text_names_the_file_under_the_key_the_sweep_reads(self) -> None:
        (row,) = json.loads(CardPicture.to_json((JOY,)) or "")
        assert (row["file"], row["kind"], row["name"]) == (JOY.file, "expression", "joy")

    def test_a_picture_recorded_before_kinds_is_a_portrait_without_a_name(self) -> None:
        text = json.dumps([{"file": FACE.file, "width": 768, "height": 1024, "size": 41200}])
        (picture,) = CardPicture.from_json(text)
        assert (picture.kind, picture.name) == (CardPictureKind.PORTRAIT, "")

    def test_a_kind_this_version_does_not_know_is_another_picture(self) -> None:
        row = {"file": FACE.file, "width": 1, "height": 1, "size": 1, "kind": "sprite-sheet"}
        (picture,) = CardPicture.from_json(json.dumps([row]))
        assert picture.kind is CardPictureKind.OTHER


class TestOf:
    def test_a_card_picture_as_a_turns_is_its_file_and_measure(self) -> None:
        assert Attachment.of(JOY) == Attachment(JOY.file, JOY.width, JOY.height, JOY.size)
