"""What the page asks for, where the answer is pure.

Three promises `web.api` makes that running the app cannot show. Every
play event has a name on the wire, with the union CLOSED — a new kind
must fail here rather than arrive as a silence. The story's typed
language reaches the page as two disjoint halves, so the composer's
menu can offer the right one at the caret. And the route table's key
says which lane a row takes, because the METHOD is the lane.
"""

from typing import get_args

from otaku.backend.api.play import Declined, Done, Failed, PlayEvent, Reasoning, Recorded, Text
from otaku.store.schema import Message
from otaku.web import api


class TestEvent:
    """`web.api.event` — one play event as the page reads it."""

    def test_every_kind_has_a_name_on_the_wire(self) -> None:
        assert api.event(Recorded(Message(role="user", body="hi")))["type"] == "recorded"
        assert api.event(Reasoning("hm"))["type"] == "reasoning"
        assert api.event(Text("word"))["type"] == "text"
        assert api.event(Declined("no model"))["type"] == "declined"
        assert api.event(Failed("the provider hung up"))["type"] == "failed"
        assert api.event(Done(reply="done", stats="7 tok/s"))["type"] == "done"

    def test_the_union_is_covered(self) -> None:
        # The match is exhaustive by construction; this is what makes
        # ADDING a kind fail here instead of shipping as a silence.
        assert {kind.__name__ for kind in get_args(PlayEvent)} == {
            "Recorded",
            "Reasoning",
            "Text",
            "Declined",
            "Failed",
            "Done",
        }

    def test_a_text_event_carries_its_text(self) -> None:
        assert api.event(Text("the light"))["text"] == "the light"

    def test_a_recorded_event_carries_the_turn(self) -> None:
        turn = api.event(Recorded(Message(id=4, role="user", body="I listen.")))["turn"]
        assert turn == {
            "id": 4,
            "role": "user",
            "body": "I listen.",
            "kind": "dialogue",
            "speaker": None,
            "provider": None,
            "model": None,
            "template": None,
        }


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


def _surface() -> dict[tuple[str, str], object]:
    """Every path the page may ask for, whichever table answers it."""
    return {**api.ROUTES, **api.FLOWS}
