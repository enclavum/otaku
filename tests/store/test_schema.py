"""The `attachments` column's text: a JSON list for a turn with
pictures, NULL for one without — never an empty list pretending to be
absent — and the same rows back. The keys are a contract with the SQL
that reads the column (`$.file` is what the sweep extracts).
"""

import json

from otaku.store.schema import Attachment, attachments_from_json, attachments_to_json

CAT = Attachment(file="pic-0001-20260918-a3f9c1e2.jpg", width=1568, height=1043, size=312044)
DOG = Attachment(file="pic-0001-20260918-7b02d4ee.png", width=800, height=600, size=90210)


class TestAttachmentsColumn:
    def test_a_turn_without_pictures_is_null(self) -> None:
        assert attachments_to_json(()) is None

    def test_null_reads_as_no_pictures(self) -> None:
        assert attachments_from_json(None) == ()
        assert attachments_from_json("") == ()

    def test_pictures_round_trip_in_order(self) -> None:
        assert attachments_from_json(attachments_to_json((CAT, DOG))) == (CAT, DOG)

    def test_the_text_names_the_file_under_the_key_the_sweep_reads(self) -> None:
        (row,) = json.loads(attachments_to_json((CAT,)) or "")
        assert row["file"] == CAT.file
        assert set(row) == {"file", "width", "height", "size"}
