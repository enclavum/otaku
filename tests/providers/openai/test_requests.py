"""How a request is written: the two streaming bodies, images on the last
message, and the prompt-cache marks."""

import base64
from dataclasses import dataclass
from typing import Any

from otaku.providers import Image
from otaku.providers.openai.requests import chat_completion_body, text_completion_body


@dataclass(frozen=True)
class _Turn:
    role: str
    body: str


def _turns(*pairs: tuple[str, str]) -> list[_Turn]:
    return [_Turn(role, body) for role, body in pairs]


def _messages(body: dict[str, object]) -> list[Any]:
    messages = body["messages"]
    assert isinstance(messages, list)
    return messages


class TestChatBody:
    def test_the_shape_of_a_streaming_chat_request(self) -> None:
        turns = _turns(("system", "s"), ("user", "u"))
        assert chat_completion_body("m", turns, {"temperature": 0.7}) == {
            "model": "m",
            "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0.7,
        }

    def test_the_messages_stay_plain_strings_without_marks_or_images(self) -> None:
        body = chat_completion_body("m", _turns(("user", "u"), ("assistant", "a")), {})
        assert all(isinstance(m["content"], str) for m in _messages(body))


class TestImages:
    def test_images_ride_the_last_message_as_parts_after_its_text(self) -> None:
        png = Image(data=b"\x89PNG", media_type="image/png")
        turns = _turns(("user", "first"), ("user", "look"))
        messages = _messages(chat_completion_body("m", turns, {}, images=[png]))
        assert messages[0] == {"role": "user", "content": "first"}
        encoded = base64.b64encode(b"\x89PNG").decode("ascii")
        assert messages[1] == {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ],
        }

    def test_an_empty_text_gets_no_empty_part(self) -> None:
        # The catalogs refuse an empty text part.
        jpeg = Image(data=b"\xff\xd8", media_type="image/jpeg")
        messages = _messages(chat_completion_body("m", _turns(("user", "")), {}, images=[jpeg]))
        assert [p["type"] for p in messages[0]["content"]] == ["image_url"]


class TestCacheMarks:
    def test_the_system_row_and_the_last_row_are_marked_the_middle_stays_plain(self) -> None:
        turns = _turns(("system", "s"), ("user", "u1"), ("assistant", "a1"), ("user", "u2"))
        messages = _messages(chat_completion_body("m", turns, {}, cache_ttl="5m"))
        assert messages[0]["content"] == [
            {"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}
        ]
        assert messages[1]["content"] == "u1" and messages[2]["content"] == "a1"
        assert messages[3]["content"] == [
            {"type": "text", "text": "u2", "cache_control": {"type": "ephemeral"}}
        ]

    def test_an_hour_is_spelled_five_minutes_is_the_default_and_is_not(self) -> None:
        messages = _messages(chat_completion_body("m", _turns(("user", "u")), {}, cache_ttl="1h"))
        assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    def test_no_ttl_marks_nothing(self) -> None:
        turns = _turns(("system", "s"), ("user", "u"))
        assert _messages(chat_completion_body("m", turns, {}, cache_ttl=None)) == [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]

    def test_a_marked_last_row_still_takes_its_images(self) -> None:
        png = Image(data=b"\x89PNG", media_type="image/png")
        turns = _turns(("user", "look"))
        messages = _messages(chat_completion_body("m", turns, {}, images=[png], cache_ttl="5m"))
        parts = messages[0]["content"]
        assert [p["type"] for p in parts] == ["text", "image_url"]
        assert any("cache_control" in p for p in parts)


class TestTextBody:
    def test_the_shape_of_a_streaming_text_request(self) -> None:
        assert text_completion_body("m", "Once upon", {"stop": ["\n"]}) == {
            "model": "m",
            "prompt": "Once upon",
            "stream": True,
            "stream_options": {"include_usage": True},
            "stop": ["\n"],
        }
