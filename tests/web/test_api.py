"""What the page asks for, where the answer is pure.

Three promises `web.api` makes that running the app cannot show. Every
play event has a name on the wire, with the union CLOSED — a new kind
must fail here rather than arrive as a silence — and every row that
lands is told as the turn the page draws, its join to the row before it
carried with it. The story's typed language reaches the page as two
disjoint halves, so the composer's menu can offer the right one at the
caret. And the route table's key says which lane a row takes, because
the METHOD is the lane.
"""

from typing import get_args

from otaku.backend.api.play import (
    AssistantReasoning,
    AssistantText,
    AssistantToolCall,
    AssistantTurn,
    Declined,
    Done,
    Failed,
    PlayEvent,
    ToolTurn,
    UserTurn,
)
from otaku.context.assembler import JoinType
from otaku.store.schema import Message, StoryMode
from otaku.web import api
from otaku.web.api import play as web_play


class TestEvent:
    """`web.api.event` — one play event as the page reads it."""

    def test_every_kind_has_a_name_on_the_wire(self) -> None:
        line = Message(role="user", body="hi")
        assert api.event(UserTurn(line, JoinType.PARAGRAPH))["type"] == "user_turn"
        assert api.event(AssistantReasoning("hm"))["type"] == "assistant_reasoning"
        assert api.event(AssistantText("word"))["type"] == "assistant_text"
        call = AssistantToolCall("note", "a plan", closed=True)
        assert api.event(call)["type"] == "assistant_tool_call"
        reply = Message(role="assistant", body="Creak.")
        assert api.event(AssistantTurn(reply, JoinType.PARAGRAPH))["type"] == "assistant_turn"
        result = Message(role="tool", kind="tool", body="Time: now", provider="system")
        assert api.event(ToolTurn(result, JoinType.PARAGRAPH))["type"] == "tool_turn"
        assert api.event(Declined("no model"))["type"] == "declined"
        assert api.event(Failed("the provider hung up"))["type"] == "failed"
        assert api.event(Done(report=None, stats="7 tok/s"))["type"] == "done"

    def test_the_union_is_covered(self) -> None:
        # The match is exhaustive by construction; this is what makes
        # ADDING a kind fail here instead of shipping as a silence.
        assert {kind.__name__ for kind in get_args(PlayEvent)} == {
            "UserTurn",
            "AssistantText",
            "AssistantReasoning",
            "AssistantToolCall",
            "AssistantTurn",
            "ToolTurn",
            "Declined",
            "Failed",
            "Done",
        }

    def test_a_text_event_carries_its_text(self) -> None:
        assert api.event(AssistantText("the light"))["text"] == "the light"
        assert api.event(AssistantReasoning("hm"))["text"] == "hm"

    def test_a_tool_call_carries_the_tool_its_piece_and_whether_it_closed(self) -> None:
        # the tool's label and who acts on it ride along where the tool
        # is known, so the page draws a call otaku runs as it streams
        assert api.event(AssistantToolCall("web-search", "tides", closed=False)) == {
            "type": "assistant_tool_call",
            "tool": "web-search",
            "label": "Web search",
            "actor": "system",
            "text": "tides",
            "closed": False,
        }
        unknown = api.event(AssistantToolCall("plan", "x", closed=True))
        assert (unknown["label"], unknown["actor"], unknown["closed"]) == ("", None, True)

    def test_a_user_turn_carries_the_row_its_join_and_the_records_note(self) -> None:
        line = Message(id=4, role="user", body="I listen.")
        happened = api.event(UserTurn(line, JoinType.INLINE, note="1d20: 17"))
        assert happened["note"] == "1d20: 17"
        assert happened["turn"] == {
            "id": 4,
            "role": "user",
            "body": "I listen.",
            "kind": "dialogue",
            "speaker": None,
            "provider": None,
            "model": None,
            "template": None,
            "attachments": [],
            "tool_label": None,
            "segments": [{"kind": "prose", "text": "I listen."}],
            "join_type": "inline",
        }
        assert api.event(UserTurn(line, JoinType.PARAGRAPH))["note"] == ""

    def test_an_assistant_turn_carries_the_row_as_stored_split_into_segments(self) -> None:
        # A question the reply ended on is in the segments, read: the
        # page poses it from there, and nothing else says so.
        body = "Creak.\n```otk-question\nGo in?\n1. Yes\n```"
        reply = Message(id=5, role="assistant", body=body)
        turn = api.event(AssistantTurn(reply, JoinType.PARAGRAPH))["turn"]
        assert turn["segments"] == [
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
        assert turn["join_type"] == "paragraph"

    def test_a_tool_turn_carries_the_tools_label_and_its_result_verbatim(self) -> None:
        result = Message(id=6, role="tool", kind="tool", body="Tides: high.", provider="web-search")
        turn = api.event(ToolTurn(result, JoinType.PARAGRAPH))["turn"]
        assert (turn["role"], turn["kind"], turn["body"]) == ("tool", "tool", "Tides: high.")
        assert turn["tool_label"] == "Web search call"
        assert turn["provider"] == "web-search"

    def test_done_carries_the_report_and_its_notice_and_no_reply(self) -> None:
        # every row landed before it, each told by its own event
        assert api.event(Done(report=None, stats="")) == {
            "type": "done",
            "stats": "",
            "report": None,
            "notice": "",
        }

    def test_declined_and_failed_carry_their_sentence(self) -> None:
        assert api.event(Declined("no model"))["reason"] == "no model"
        assert api.event(Failed("hung up"))["reason"] == "hung up"


class TestRows:
    """`web.api.play.rows` — the stored turns as the page reads them,
    each joined to the one before it by the join rule."""

    def test_each_row_carries_how_it_joins_the_one_before(self) -> None:
        story = StoryMode.STORY
        chain = [
            Message(id=1, role="user", body="She waited for", mode=story),
            Message(id=2, role="assistant", body="the bus.", mode=story),
            Message(id=3, role="user", body="Then?"),
            Message(id=4, role="assistant", body="\n\nNothing.", mode=story),
        ]
        assert [row["join_type"] for row in web_play.rows(chain)] == [
            "paragraph",
            "inline",
            "paragraph",
            "paragraph",
        ]

    def test_a_tool_row_is_named_for_the_reader_and_every_other_row_is_not(self) -> None:
        chain = [
            Message(id=1, role="assistant", body="```otk-system-info\n```"),
            Message(id=2, role="tool", kind="tool", body="Time: now", provider="system-info"),
        ]
        assert [row["tool_label"] for row in web_play.rows(chain)] == [None, "System info call"]


class TestSyntax:
    """`web.api.syntax` — the story's typed language, which is what the
    composer's menu offers and the help sheet lists. Not commands: those
    are endpoints, and a button is how the page reaches one."""

    def test_every_row_carries_what_a_menu_needs(self) -> None:
        language = api.syntax()
        for row in language["openers"] + language["inliners"]:
            assert set(row) == {"token", "args"}
            assert row["token"].startswith(("/", "…"))

    def test_the_two_halves_are_disjoint(self) -> None:
        # A word opens a line or rides inside one. The `… ` mark is what
        # the shared table divides them by, and both halves reach the
        # page so the menu can offer the right one at the caret.
        language = api.syntax()
        assert all(not row["token"].startswith("…") for row in language["openers"])
        assert all(row["token"].startswith("…") for row in language["inliners"])
        assert language["openers"] and language["inliners"]

    def test_no_command_is_in_it(self) -> None:
        # A command reaching the composer's menu would offer a word the
        # box does not take: everything typed there is story.
        tokens = {row["token"] for row in api.syntax()["openers"]}
        assert not tokens & {"/stories", "/model", "/set", "/help"}

    def test_a_line_with_no_framing_is_explained(self) -> None:
        assert api.syntax()["prose"]


class TestRoutes:
    """`web.api.ROUTES` and `web.api.FLOWS` — the whole surface, keyed by
    method and path. The METHOD is the lane, so what each row promises
    about the session is readable from its key alone."""

    def test_the_method_is_the_lane(self) -> None:
        # A GET only reads; anything that MOVES the story is another
        # method. A read filed as a POST would queue behind a reply and
        # leave every screen dead while the model talks.
        moving = {"/api/stories", "/api/history"}
        surface = _surface()
        for method, template in surface:
            assert method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
            if method == "GET":
                assert template not in moving or ("POST", template) in surface

    def test_every_template_is_absolute_and_parameterless_where_it_should_be(self) -> None:
        for _, template in _surface():
            assert template.startswith("/api/")
            assert not template.endswith("/")

    def test_no_path_is_answered_by_both_tables(self) -> None:
        # The server matches one sorted list built from both, so a path
        # in both would let the compile order decide whether its handler
        # is handed `Pending` — a difference no request could show.
        assert set(api.ROUTES) & set(api.FLOWS) == set()

    def test_the_web_search_key_has_its_read_and_its_write(self) -> None:
        # one box in the page: what stands is a read, the key a PATCH
        assert ("GET", "/api/integrations/web_search") in api.ROUTES
        assert ("PATCH", "/api/integrations/web_search") in api.ROUTES


def _surface() -> dict[tuple[str, str], object]:
    """Every path the page may ask for, whichever table answers it."""
    return {**api.ROUTES, **api.FLOWS}
