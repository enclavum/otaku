"""Free prose into a story.

`split_segments` dismantles prose into message-sized segments,
reproducing the text verbatim; `parse_plaintext` wraps them as narration
turns and returns None only when there is nothing to import.
"""

from otaku.transfer.plaintext import parse_plaintext, split_segments


class TestParsePlaintext:
    def test_prose_becomes_narration_turns(self) -> None:
        parsed = parse_plaintext("Первый абзац.\n\nВторой абзац.")
        assert parsed is not None
        assert [(m.role, m.kind, m.body) for m in parsed.messages] == [
            ("user", "narration", "Первый абзац."),
            ("user", "narration", "Второй абзац."),
        ]

    def test_nothing_to_import_is_none(self) -> None:
        assert parse_plaintext("") is None
        assert parse_plaintext("   \n\n  ") is None


class TestSplitSegments:
    def test_a_plain_paragraph_stays_whole(self) -> None:
        assert split_segments("Ночь была тихой и длинной.") == ["Ночь была тихой и длинной."]

    def test_speech_splits_from_narration(self) -> None:
        text = 'Он замер у двери. "Открой дверь," сказала она.'
        assert split_segments(text) == ["Он замер у двери.", '"Открой дверь," сказала она.']

    def test_an_attribution_tag_stays_with_its_quote(self) -> None:
        text = '"Открой дверь," сказала она устало.'
        assert split_segments(text) == ['"Открой дверь," сказала она устало.']

    def test_paragraphs_split_at_blank_lines(self) -> None:
        assert split_segments("Первый абзац.\n\nВторой абзац.") == [
            "Первый абзац.",
            "Второй абзац.",
        ]

    def test_dash_dialogue_lines_become_turns(self) -> None:
        text = "Он вошёл в зал.\n— Кто здесь? — спросил он.\n— Я, — ответила тень."
        segments = split_segments(text)
        assert segments == [
            "Он вошёл в зал.",
            "— Кто здесь? — спросил он.",
            "— Я, — ответила тень.",
        ]

    def test_long_narration_splits_at_sentence_boundaries(self) -> None:
        text = " ".join(f"Предложение номер {i} тянется дальше." for i in range(40))
        segments = split_segments(text)
        assert len(segments) > 1
        assert " ".join(segments) == text

    def test_text_is_reproduced_verbatim(self) -> None:
        text = '"Стой!" крикнул он. Она замерла на месте.'
        assert " ".join(split_segments(text)) == text
