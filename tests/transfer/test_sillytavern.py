"""SillyTavern chats, read into the same `StoryExport` our document parses to."""

import json

from otaku.transfer.sillytavern import parse_sillytavern


class TestParseSillytavern:
    HEADER = json.dumps({"user_name": "You", "character_name": "Elara"})

    def line(self, **kw: object) -> str:
        return json.dumps(kw, ensure_ascii=False)

    def test_parses_visible_messages(self) -> None:
        text = "\n".join(
            [
                self.HEADER,
                self.line(name="You", is_user=True, mes="Hello."),
                self.line(name="Elara", is_user=False, mes="Hi."),
            ]
        )
        parsed = parse_sillytavern(text)
        assert parsed is not None
        turns = parsed.messages
        assert [(t.role, t.body) for t in turns] == [("user", "Hello."), ("assistant", "Hi.")]

    def test_names_become_speakers_but_role_words_do_not(self) -> None:
        text = "\n".join(
            [
                self.HEADER,
                self.line(name="You", is_user=True, mes="Hello."),
                self.line(name="Elara", is_user=False, mes="Hi."),
            ]
        )
        parsed = parse_sillytavern(text)
        assert parsed is not None
        assert parsed.messages[0].speaker is None
        assert parsed.messages[1].speaker == "Elara"

    def test_hidden_and_malformed_lines_are_skipped(self) -> None:
        text = "\n".join(
            [
                self.HEADER,
                self.line(name="Elara", mes="Visible."),
                self.line(name="Elara", mes="Hidden.", is_system=True),
                "not json at all",
                self.line(name="Elara", mes=""),
            ]
        )
        parsed = parse_sillytavern(text)
        assert parsed is not None
        assert len(parsed.messages) == 1

    def test_a_non_st_file_is_not_a_chat(self) -> None:
        assert parse_sillytavern("# markdown, not jsonl") is None
        assert parse_sillytavern(json.dumps({"mes": "no header"})) is None
