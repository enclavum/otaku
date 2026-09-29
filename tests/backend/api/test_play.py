"""The play API's pure edges: how a stored turn joins the one before it,
as a frontend drawing the canvas reads it (`join_kind`, the seam rule's
word), and a stored body split for a frontend that parses nothing
(`segments`)."""

from otaku.backend.api.play import join_kind, segments
from otaku.store.schema import Message


class TestJoinKind:
    def test_the_seam_rules_two_words(self) -> None:
        before = Message(role="user", body="She waited for")
        assert join_kind(before, Message(role="assistant", body=" the bus.")) == "inline"
        assert join_kind(before, Message(role="assistant", body="The bus.")) == "paragraph"
        assert join_kind(None, Message(role="user", body=" the bus.")) == "paragraph"

    def test_a_tool_turn_and_the_row_after_it_are_paragraphs(self) -> None:
        result = Message(role="tool", kind="tool", body="Time: now ", provider="system")
        assert join_kind(Message(role="assistant", body="Look "), result) == "paragraph"
        assert join_kind(result, Message(role="assistant", body=" It is six.")) == "paragraph"


class TestSegments:
    def test_prose_and_each_call_as_its_tools_facts(self) -> None:
        body = "Creak.\n```otk-question\nGo in?\n1. Yes\n```"
        assert segments(body) == [
            {"kind": "prose", "text": "Creak."},
            {
                "kind": "tool_call",
                "tool": "question",
                "label": "",
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
        (call,) = segments("```otk-search\ntides\n```")
        assert (call["tool"], call["label"], call["actor"]) == ("search", "Web search", "system")

    def test_plain_prose_is_one_segment_and_an_empty_body_none(self) -> None:
        assert segments("The hall glows.") == [{"kind": "prose", "text": "The hall glows."}]
        assert segments("") == []
