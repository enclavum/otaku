"""The play API's pure edges: how a stored turn joins the one before it,
as a frontend drawing the canvas reads it (`join_type`, the join rule's
word), how a line typed now would join the newest row (`next_join`, the
same rule, a line with no break of its own in the layout's mode), and a
stored body split for a frontend that parses nothing (`segments`)."""

from types import SimpleNamespace
from typing import cast

from otaku.backend.api.play import join_type, next_join, segments
from otaku.backend.session import Session
from otaku.store.schema import Layout, Message, StoryMode

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

    def test_a_tool_turn_runs_on_in_story_mode_as_any_row(self) -> None:
        # story mode draws no tool's turn: the reply after it goes on in the
        # paragraph the call interrupted
        result = Message(
            role="tool", kind="tool", body="Time: now", provider="system-info", mode=STORY
        )
        look = Message(role="assistant", body="Look", mode=STORY)
        assert join_type(look, result) == "inline"
        assert join_type(result, Message(role="assistant", body="It is six.", mode=STORY)) == (
            "inline"
        )
        chat_result = Message(role="tool", kind="tool", body="Time: now", provider="system-info")
        assert join_type(chat_result, Message(role="assistant", body="Six.")) == "paragraph"


class TestNextJoin:
    def test_no_row_is_a_story_to_begin(self) -> None:
        assert next_join(session([], Layout.CANVAS)) is None

    def test_after_a_row_written_in_the_text_a_line_runs_on(self) -> None:
        reply = Message(role="assistant", body="It rained.", mode=STORY)
        assert next_join(session([reply], Layout.CANVAS)) == "inline"
        assert next_join(session([reply], Layout.NOVEL)) == "inline"

    def test_a_break_at_the_rows_end_opens_a_paragraph(self) -> None:
        line = Message(role="user", body="It rained.\n\n", mode=STORY)
        assert next_join(session([line], Layout.CANVAS)) == "paragraph"

    def test_after_a_row_played_in_chat_a_line_opens_a_paragraph(self) -> None:
        reply = Message(role="assistant", body="It rained.")
        assert next_join(session([reply], Layout.CANVAS)) == "paragraph"

    def test_in_a_chat_layout_a_line_opens_a_paragraph(self) -> None:
        reply = Message(role="assistant", body="It rained.", mode=STORY)
        for layout in (Layout.MANUSCRIPT, Layout.CHAT):
            assert next_join(session([reply], layout)) == "paragraph", layout

    def test_a_cards_row_stands_alone(self) -> None:
        greeting = Message(role="assistant", body="Welcome.", provider="card", mode=STORY)
        assert next_join(session([greeting], Layout.CANVAS)) == "paragraph"


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


def session(rows: list[Message], layout: Layout) -> Session:
    """A stand-in session holding what `next_join` reads: the rows played,
    and the layout in force."""
    return cast(Session, SimpleNamespace(_messages=rows, layout=layout))
