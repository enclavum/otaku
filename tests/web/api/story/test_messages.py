"""A play event as the page reads it (`web.api.story.messages.event`).

Every event has a name on the wire, with the union CLOSED — a new kind
must fail here rather than arrive as a silence — and each carries what
the backend handed it, as JSON writes it: a message landed whole, the
reply's first piece its join, a tool call its label, who acts on it and
what the status row says, the reply's report its facts and its notice.
"""

from dataclasses import replace
from typing import get_args

from otaku.backend.api import Actor, JoinType
from otaku.backend.story.messages.schemas import (
    AssistantMessage,
    AssistantReasoning,
    AssistantText,
    AssistantToolCall,
    Declined,
    Done,
    Failed,
    PlayEvent,
    ReplyReport,
    ToolMessage,
    UserMessage,
    _as_message,
)
from otaku.backend.story.messages.schemas import Message as Handed
from otaku.backend.story.messages.services import _listed
from otaku.providers import Stats
from otaku.store.schema import Message
from otaku.web.api.story.messages import event


class TestEvent:
    def test_every_kind_has_a_name_on_the_wire(self) -> None:
        line, reply, result = handed(
            Message(role="user", body="hi"),
            Message(role="assistant", body="Creak."),
            Message(role="tool", kind="tool", body="Time: now", provider="system-info"),
        )
        assert event(UserMessage(line))["type"] == "user_message"
        assert event(AssistantReasoning("hm"))["type"] == "assistant_reasoning"
        assert event(AssistantText("word"))["type"] == "assistant_text"
        assert event(_call())["type"] == "assistant_tool_call"
        assert event(AssistantMessage(reply))["type"] == "assistant_message"
        assert event(ToolMessage(result))["type"] == "tool_message"
        assert event(Declined("no model"))["type"] == "declined"
        assert event(Failed("the provider hung up"))["type"] == "failed"
        assert event(Done(report=None, stats="7 tok/s"))["type"] == "done"

    def test_the_union_is_covered(self) -> None:
        # The match is exhaustive by construction; this is what makes
        # ADDING a kind fail here instead of shipping as a silence.
        assert {kind.__name__ for kind in get_args(PlayEvent)} == {
            "UserMessage",
            "AssistantText",
            "AssistantReasoning",
            "AssistantToolCall",
            "AssistantMessage",
            "ToolMessage",
            "Declined",
            "Failed",
            "Done",
        }

    def test_the_replys_first_piece_carries_its_join_and_every_later_piece_none(self) -> None:
        assert event(AssistantText("the light", JoinType.INLINE)) == {
            "type": "assistant_text",
            "text": "the light",
            "join_type": "inline",
        }
        assert event(AssistantText("on"))["join_type"] is None
        assert event(AssistantReasoning("hm"))["text"] == "hm"

    def test_a_tool_call_carries_what_the_backend_says_of_it(self) -> None:
        assert event(_call()) == {
            "type": "assistant_tool_call",
            "tool": "web-search",
            "label": "Web search",
            "actor": "system",
            "status": "calling the web search tool",
            "text": "tides",
            "closed": False,
        }
        unknown = event(AssistantToolCall("plan", "x", True, "", None, "calling the plan tool"))
        assert (unknown["label"], unknown["actor"], unknown["closed"]) == ("", None, True)

    def test_a_user_message_carries_the_message_and_the_records_note(self) -> None:
        (line,) = handed(Message(role="user", body="I listen."))
        happened = event(UserMessage(line, note="1d20: 17"))
        assert happened["note"] == "1d20: 17"
        assert happened["message"]["body"] == "I listen."
        assert happened["message"]["segments"] == [{"kind": "prose", "text": "I listen."}]
        assert happened["message"]["join_type"] == "paragraph"
        assert event(UserMessage(line))["note"] == ""

    def test_an_assistant_message_carries_the_body_as_stored_and_its_split(self) -> None:
        # A question the reply ended on is in the segments, read: the
        # page draws it from there.
        body = "Creak.\n```otk-question\nGo in?\n1. Yes\n```"
        (reply,) = handed(Message(role="assistant", body=body))
        message = event(AssistantMessage(reply))["message"]
        assert message["body"] == body
        assert message["segments"][1]["question"] == "Go in?"
        assert message["segments"][1]["options"] == ["Yes"]

    def test_a_tool_message_carries_the_tools_label_and_its_result_verbatim(self) -> None:
        call, result = handed(
            Message(role="assistant", body="```otk-web-search\ntides\n```"),
            Message(role="tool", kind="tool", body="Tides: high.", provider="web-search"),
        )
        message = event(ToolMessage(result))["message"]
        assert (message["role"], message["kind"], message["body"]) == (
            "tool",
            "tool",
            "Tides: high.",
        )
        assert message["tool_label"] == "Web search call"
        assert message["provider"] == "web-search"
        # the reply holding the call and the result are one unit
        assert message["unit"] == call.id

    def test_done_without_a_report_says_nothing(self) -> None:
        assert event(Done(report=None, stats="")) == {
            "type": "done",
            "stats": "",
            "report": None,
            "notice": "",
        }

    def test_done_carries_the_reports_facts_the_derived_ones_included(self) -> None:
        stats = Stats(
            prompt_tokens=400,
            completion_tokens=30,
            first_token_seconds=1.0,
            total_seconds=4.0,
            finish_reason="length",
        )
        report = ReplyReport(stats, 1000)
        done = event(Done(report=report, stats="[ total 4.0s ]"))
        assert done["stats"] == "[ total 4.0s ]"
        assert done["report"]["prompt_tokens"] == 400
        assert done["report"]["context_in_force"] == 1000
        assert done["report"]["context_used"] == 40
        assert done["report"]["rate"] == 10.0
        assert done["report"]["truncated"] is True
        # a reply cut short says so
        assert done["notice"] == report.notice != ""

    def test_declined_and_failed_carry_their_sentence(self) -> None:
        assert event(Declined("no model"))["reason"] == "no model"
        assert event(Failed("hung up"))["reason"] == "hung up"


def handed(*rows: Message) -> list[Handed]:
    """Store rows as the messages an event carries — built as the backend
    builds them, each numbered by its position."""
    chain = [replace(row, id=index + 1) for index, row in enumerate(rows)]
    return [_as_message(listed.row, listed.previous, listed.unit) for listed in _listed(chain)]


def _call() -> AssistantToolCall:
    """A piece of a call otaku runs, as the backend tells it."""
    return AssistantToolCall(
        "web-search", "tides", False, "Web search", Actor.SYSTEM, "calling the web search tool"
    )
