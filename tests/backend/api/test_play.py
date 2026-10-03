"""The play API's pure edges: how a stored turn joins the one before it,
as a frontend drawing the canvas reads it (`join_type`, the join rule's
word), and a stored body split for a frontend that parses nothing
(`segments`)."""

from otaku.backend.api.play import join_type, segments
from otaku.store.schema import Message, StoryMode

STORY = StoryMode.STORY


class TestJoinType:
    def test_the_join_rules_two_words(self) -> None:
        before = Message(role="user", body="She waited for", mode=STORY)
        assert join_type(before, Message(role="assistant", body="the bus.", mode=STORY)) == (
            "inline"
        )
        assert join_type(before, Message(role="assistant", body="\n\nThe bus.", mode=STORY)) == (
            "paragraph"
        )
        assert join_type(None, Message(role="user", body="the bus.", mode=STORY)) == "paragraph"

    def test_a_chat_row_is_a_paragraph(self) -> None:
        before = Message(role="user", body="She waited for")
        assert join_type(before, Message(role="assistant", body="the bus.")) == "paragraph"

    def test_a_tool_turn_and_the_row_after_it_are_paragraphs(self) -> None:
        result = Message(
            role="tool", kind="tool", body="Time: now", provider="system-info", mode=STORY
        )
        look = Message(role="assistant", body="Look", mode=STORY)
        assert join_type(look, result) == "paragraph"
        assert join_type(result, Message(role="assistant", body="It is six.", mode=STORY)) == (
            "paragraph"
        )


class TestSegments:
    def test_prose_and_each_call_as_its_tools_facts(self) -> None:
        body = "Creak.\n```otk-question\nGo in?\n1. Yes\n```"
        assert segments(body) == [
            {"kind": "prose", "text": "Creak."},
            {
                "kind": "tool_call",
                "tool": "question",
                "label": "Questions",
                "actor": "user",
                "text": "Go in?\n1. Yes",
                "question": "Go in?",
                "options": ["Yes"],
            },
        ]

    def test_a_call_of_a_tool_this_build_does_not_have_is_a_call_all_the_same(self) -> None:
        assert segments("```otk-plan\nlater\n```") == [
            {"kind": "tool_call", "tool": "plan", "text": "later"}
        ]

    def test_a_call_otaku_runs_carries_who_acts_on_it(self) -> None:
        (call,) = segments("```otk-web-search\ntides\n```")
        assert (call["tool"], call["label"], call["actor"]) == (
            "web-search",
            "Web search",
            "system",
        )

    def test_plain_prose_is_one_segment_and_an_empty_body_none(self) -> None:
        assert segments("The hall glows.") == [{"kind": "prose", "text": "The hall glows."}]
        assert segments("") == []
