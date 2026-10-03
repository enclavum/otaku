"""A support request (`backend.api.support`): the reader's message, and
the address to answer at where one is given, posted to otaku.sh's
support desk with the app naming itself and its release — the desk
answers 204, which is no answer to read. What the desk would drop is
refused before anything leaves: an empty or too long message, an
address that is none; a desk that fails is a refusal too. `/support
TEXT` sends the line whole, an address in it included."""

import pytest

from otaku import __version__
from otaku.backend.api import support as api_support
from otaku.backend.session import Refused
from otaku.integrations import MAX_SUPPORT_MESSAGE
from otaku.integrations import support as desk
from scenarios.support.harness import App
from scenarios.support.server import ModelServer

DESK = "/api/support"


@pytest.fixture(autouse=True)
def _desk_at_the_scripted_server(server: ModelServer, monkeypatch: pytest.MonkeyPatch) -> None:
    """The support desk at the scripted server's door, never the site's."""
    monkeypatch.setattr(desk, "SUPPORT_ENDPOINT", server.url.removesuffix("/v1") + DESK)


class TestSupport:
    def test_a_request_is_posted_with_its_address_and_the_app_named(self, app: App) -> None:
        api_support.send_support_request(app.session, "  The canvas froze.  ", " me@example.com ")
        assert app.server.posts[-1] == DESK
        assert app.server.requests[-1] == {
            "message": "The canvas froze.",
            "email": "me@example.com",
        }
        assert app.server.request_headers[-1]["User-Agent"] == f"otaku/{__version__}"

    def test_without_an_address_none_is_sent(self, app: App) -> None:
        api_support.send_support_request(app.session, "Thanks for the canvas.")
        assert app.server.requests[-1] == {"message": "Thanks for the canvas."}

    def test_what_the_desk_would_drop_is_refused_before_anything_leaves(self, app: App) -> None:
        for body, email in (
            ("", ""),
            ("   \n", ""),
            ("x" * (MAX_SUPPORT_MESSAGE + 1), ""),
            ("Hi", "me@localhost"),
            ("Hi", "me"),
            ("Hi", "a b@c.de"),
            ("Hi", "x" * 195 + "@a.com"),
        ):
            with pytest.raises(Refused):
                api_support.send_support_request(app.session, body, email)
        assert DESK not in app.server.posts
        # at the limit, it goes
        api_support.send_support_request(app.session, "x" * MAX_SUPPORT_MESSAGE)
        assert app.server.posts[-1] == DESK

    def test_a_desk_that_fails_is_a_refusal(self, app: App) -> None:
        app.server.support_status = 500
        with pytest.raises(Refused):
            api_support.send_support_request(app.session, "Hi")

    def test_the_command_sends_the_line_whole(self, app: App) -> None:
        app.play("/support The canvas froze — me@example.com")
        assert app.server.posts[-1] == DESK
        assert app.server.requests[-1] == {"message": "The canvas froze — me@example.com"}
