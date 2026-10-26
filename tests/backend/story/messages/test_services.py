"""A stored message as both frontends are handed it: how it joins the
one before it, as a frontend drawing the canvas reads it (`join_type`,
the join rule), how a line typed now would join the newest row
(`next_join`, the same rule, a line with no break of its own in the
layout's mode), a stored body split for a frontend that parses nothing
(`segments`) — a reply into prose and its calls, a user's line into its
story and its words with the model, a chapter into its heading — what a tool's message is called
(`tool_label`), and the question the story stands on (`posed_question`)."""

from otaku.backend.story.messages.services import (
    join_type,
    next_join,
    posed_question,
    segments,
    tool_label,
)
from otaku.context.assembler import JoinType
from otaku.store.schema import Layout, Message, StoryMode

STORY = StoryMode.STORY
QUESTIONS = frozenset({"question"})


class TestJoinType:
    def test_the_join_rules_two_words(self) -> None:
        before = Message(role="user", body="She waited for", mode=STORY)
        assert (
            join_type(before, Message(role="assistant", body="the bus.", mode=STORY))
            is JoinType.INLINE
        )
        assert (
            join_type(before, Message(role="assistant", body="\n\nThe bus.", mode=STORY))
            is JoinType.PARAGRAPH
        )
        assert (
            join_type(None, Message(role="user", body="the bus.", mode=STORY)) is JoinType.PARAGRAPH
        )

    def test_a_chat_row_is_a_paragraph(self) -> None:
        before = Message(role="user", body="She waited for")
        assert join_type(before, Message(role="assistant", body="the bus.")) is JoinType.PARAGRAPH

    def test_a_tool_turn_runs_on_in_story_mode_as_any_row(self) -> None:
        # story mode draws no tool's turn: the reply after it goes on in the
        # paragraph the call interrupted
        result = Message(
            role="tool", kind="tool", body="Time: now", provider="system-info", mode=STORY
        )
        look = Message(role="assistant", body="Look", mode=STORY)
        assert join_type(look, result) is JoinType.INLINE
        assert (
            join_type(result, Message(role="assistant", body="It is six.", mode=STORY))
            is JoinType.INLINE
        )
        chat_result = Message(role="tool", kind="tool", body="Time: now", provider="system-info")
        assert join_type(chat_result, Message(role="assistant", body="Six.")) is JoinType.PARAGRAPH


class TestNextJoin:
    def test_no_row_is_a_story_to_begin(self) -> None:
        assert next_join(None, Layout.CANVAS) is None

    def test_after_a_row_written_in_the_text_a_line_runs_on(self) -> None:
        reply = Message(role="assistant", body="It rained.", mode=STORY)
        assert next_join(reply, Layout.CANVAS) is JoinType.INLINE
        assert next_join(reply, Layout.NOVEL) is JoinType.INLINE

    def test_a_break_at_the_rows_end_opens_a_paragraph(self) -> None:
        line = Message(role="user", body="It rained.\n\n", mode=STORY)
        assert next_join(line, Layout.CANVAS) is JoinType.PARAGRAPH

    def test_after_a_row_played_in_chat_a_line_opens_a_paragraph(self) -> None:
        reply = Message(role="assistant", body="It rained.")
        assert next_join(reply, Layout.CANVAS) is JoinType.PARAGRAPH

    def test_in_a_chat_layout_a_line_opens_a_paragraph(self) -> None:
        reply = Message(role="assistant", body="It rained.", mode=STORY)
        for layout in (Layout.MANUSCRIPT, Layout.CHAT):
            assert next_join(reply, layout) is JoinType.PARAGRAPH, layout

    def test_a_cards_row_stands_alone(self) -> None:
        greeting = Message(role="assistant", body="Welcome.", provider="card", mode=STORY)
        assert next_join(greeting, Layout.CANVAS) is JoinType.PARAGRAPH

    def test_a_chapter_stands_alone(self) -> None:
        heading = Message(role="user", body="/chapter The Flood", kind="chapter", mode=STORY)
        prose = Message(role="assistant", body="and the water rose.", mode=STORY)
        assert join_type(heading, prose) is JoinType.PARAGRAPH
        assert join_type(prose, heading) is JoinType.PARAGRAPH
        assert next_join(heading, Layout.CANVAS) is JoinType.PARAGRAPH


class TestSegments:
    def test_prose_and_each_call_as_its_tools_facts(self) -> None:
        body = "Creak.\n```otk-question\nGo in?\n1. Yes\n```"
        assert segments(reply(body)) == [
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
        assert segments(reply("```otk-plan\nlater\n```")) == [
            {"kind": "tool_call", "tool": "plan", "text": "later"}
        ]

    def test_a_call_otaku_runs_carries_who_acts_on_it(self) -> None:
        (call,) = segments(reply("```otk-web-search\ntides\n```"))
        assert (call["tool"], call["label"], call["actor"]) == (
            "web-search",
            "Web search",
            "system",
        )

    def test_plain_prose_is_one_segment_and_an_empty_body_none(self) -> None:
        assert segments(reply("The hall glows.")) == [{"kind": "prose", "text": "The hall glows."}]
        assert segments(reply("")) == []

    def test_a_users_line_splits_into_its_story_and_its_words_with_the_model(self) -> None:
        assert segments(line("I wait /cue make it rain")) == [
            {"kind": "prose", "text": "I wait /cue"},
            {"kind": "ooc", "text": " make it rain"},
        ]
        assert segments(line("/you Mara")) == [
            {"kind": "prose", "text": "/you"},
            {"kind": "ooc", "text": " Mara"},
        ]
        # a fence in a line is no call: a line is not a reply
        assert segments(line("```otk-note\nx\n```")) == [
            {"kind": "prose", "text": "```otk-note\nx\n```"}
        ]

    def test_a_chapter_splits_into_its_numbered_heading(self) -> None:
        parts = segments(line("/chapter The Flood", chapter=3))
        assert [part["kind"] for part in parts] == ["chapter_number", "prose", "chapter_title"]
        assert "3" in str(parts[0]["text"]) and parts[-1]["text"] == "The Flood"
        # a number not counted yet: the line as typed
        assert segments(line("/chapter The Flood")) == [
            {"kind": "prose", "text": "/chapter The Flood"}
        ]


class TestToolLabel:
    def test_a_tool_message_is_named_for_the_reader_and_every_other_is_not(self) -> None:
        result = Message(role="tool", kind="tool", body="Time: now", provider="system-info")
        assert tool_label(result) == "System info call"
        assert tool_label(Message(role="assistant", body="```otk-system-info\n```")) is None
        assert tool_label(Message(role="user", body="What time is it?")) is None


class TestPosedQuestion:
    """`posed_question` — the question the story stands on, read off the
    newest message: the questions tool on, and a reply whose last call is
    a question, nothing after it but notes and whitespace."""

    def test_a_reply_ending_on_a_question(self) -> None:
        body = "The door.\n```otk-question\nGo in?\n1. Yes\n2. No\n```"
        found = posed_question(reply(body), QUESTIONS)
        assert found is not None and (found.question, found.options) == ("Go in?", ("Yes", "No"))

    def test_none_while_the_questions_tool_is_off(self) -> None:
        assert posed_question(reply("```otk-question\nGo in?\n1. Yes\n```"), frozenset()) is None

    def test_notes_and_whitespace_after_it_do_not_answer_it(self) -> None:
        body = "```otk-question\nGo in?\n1. Yes\n```\n```otk-note\nKey.\n```\n\n"
        assert posed_question(reply(body), QUESTIONS) is not None

    def test_prose_after_it_means_the_model_went_on(self) -> None:
        body = "```otk-question\nGo in?\n1. Yes\n```\nIt opens."
        assert posed_question(reply(body), QUESTIONS) is None

    def test_a_call_of_a_tool_this_build_does_not_know_after_it_means_it_went_on(self) -> None:
        body = "```otk-question\nGo in?\n1. Yes\n```\n```otk-plan\nlater\n```"
        assert posed_question(reply(body), QUESTIONS) is None

    def test_a_line_of_the_readers_or_no_message_means_none(self) -> None:
        assert posed_question(Message(role="user", body="Yes"), QUESTIONS) is None
        assert posed_question(None, QUESTIONS) is None


def reply(body: str) -> Message:
    return Message(role="assistant", body=body)


def line(body: str, *, chapter: int | None = None) -> Message:
    return Message(role="user", body=body, chapter=chapter)
