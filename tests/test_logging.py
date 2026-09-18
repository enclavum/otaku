"""Log-view rendering — the pure day-name handling.

The contract: `resolve_day` turns a `logs` DAY argument into the stamp
the logs name their files with (YYYYMMDD) — today's when the argument
is absent, either spelling (bare or dashed) when given, and None for
anything else; `dashed` prints a stamp the human way; and `day_rows`
shapes the `--list` rows, one dashed day and its size each.
"""

from datetime import datetime

from otaku.logging import dashed, day_rows, elide_pictures, parts_text, resolve_day


class TestResolveDay:
    def test_an_absent_day_is_today(self) -> None:
        before = datetime.now().astimezone().strftime("%Y%m%d")
        stamp = resolve_day(None)
        after = datetime.now().astimezone().strftime("%Y%m%d")
        assert stamp in (before, after)  # both, lest the test straddle midnight

    def test_the_dashed_form_becomes_the_stamp(self) -> None:
        assert resolve_day("2026-07-25") == "20260725"

    def test_a_bare_stamp_passes_through(self) -> None:
        assert resolve_day("20260725") == "20260725"

    def test_anything_else_is_none(self) -> None:
        assert resolve_day("yesterday") is None
        assert resolve_day("2026-7-25") is None


class TestDashed:
    def test_a_stamp_prints_the_human_way(self) -> None:
        assert dashed("20260725") == "2026-07-25"


class TestDayRows:
    def test_one_row_per_day_with_its_size(self) -> None:
        rows = day_rows([("20260725", 1234), ("20260726", 56)])
        assert rows == ["2026-07-25       1,234 B", "2026-07-26          56 B"]


class TestElidePictures:
    """A request's pictures are logged as their size, never their bytes."""

    def test_a_data_url_becomes_a_note_of_its_size(self) -> None:
        body = {
            "model": "m",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                    ],
                },
            ],
        }
        elided = elide_pictures(body)
        parts = elided["messages"][0]["content"]  # type: ignore[index]
        assert parts[0] == {"type": "text", "text": "look"}
        assert parts[1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,<4 chars elided>"},
        }
        assert elided["model"] == "m"

    def test_plain_messages_and_a_body_without_any_come_back_as_they_are(self) -> None:
        plain = {"messages": [{"role": "user", "content": "look"}]}
        assert elide_pictures(plain) == plain
        assert elide_pictures({"prompt": "Once"}) == {"prompt": "Once"}

    def test_the_request_itself_is_not_changed(self) -> None:
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BB"}}
                    ],
                }
            ]
        }
        elide_pictures(body)
        assert body["messages"][0]["content"][0]["image_url"]["url"] == "data:image/jpeg;base64,BB"  # type: ignore[index]


class TestPartsText:
    """A parts-form message reads as one line, a picture marked by its
    type and size — so a reader of the log sees that one rode the turn."""

    def test_a_picture_is_marked_beside_the_words(self) -> None:
        parts = [
            {"type": "text", "text": "What is this?"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,<312,044 chars elided>"},
            },
        ]
        assert parts_text(parts) == "What is this? [picture image/jpeg, 312,044 chars]"

    def test_a_line_written_before_eliding_is_sized_from_its_data(self) -> None:
        parts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
        assert parts_text(parts) == "[picture image/png, 4 chars]"

    def test_text_parts_alone_read_as_before(self) -> None:
        parts = [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}]
        assert parts_text(parts) == "s"
