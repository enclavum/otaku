"""The integrations as a frontend reaches them.

The web search key: ONE box in either frontend, and ONE key — Tavily's
or Exa's, which engine it belongs to told from its shape only when a
search is asked (`integrations.WebSearch`). It goes sealed into the web
search section of integrations.toml, which names no engine, and lives in
the app at once; "" forgets it; a section a hand edit dropped is founded
by the save. The value goes in and never comes back out through a read:
`web_search.get` says only whether a key is held.

A support request: the reader's message, and the address to answer at
where one is given, posted to otaku.sh's support desk with the app
naming itself and its release — the desk answers 204, which is no answer
to read. What the desk would drop is refused before anything leaves: an
empty or too long message, an address that is none; a desk that fails
is a refusal too. `/support TEXT` sends the line whole, an address in it
included.
"""

import tomllib

import pytest

from otaku import __version__
from otaku.backend import launch as backend_launch
from otaku.backend.api import Api, Refused
from otaku.backend.app.integrations.schemas import WebSearch
from otaku.encryption import unseal
from otaku.integrations import SupportDesk, web_search
from scenarios.api.story.test_messages import _after_the_tool
from scenarios.support.harness import App
from scenarios.support.server import ModelServer

DESK = "/api/support"

SEARCH = "/search"


class TestWebSearchKey:
    def test_nothing_stands_until_a_key_is_saved(self, app: App) -> None:
        assert app.api.integrations.web_search.get() == WebSearch(False)
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert file == {"web_search": {"api_key": ""}}

    def test_a_key_is_sealed_into_the_one_section_whichever_engines(self, app: App) -> None:
        for key in ("tvly-dev-123", "0f3a-2b"):
            app.api.integrations.web_search.update(api_key=f" {key} ")
            assert app.api.integrations.web_search.get() == WebSearch(True)
            raw = app.paths.integrations_file.read_text()
            assert key not in raw  # never plain in the file
            file = tomllib.loads(raw)
            # one section, no engine named in it
            assert set(file) == {"web_search"} and set(file["web_search"]) == {"api_key"}
            assert _unsealed(app, file["web_search"]["api_key"]) == key

    def test_a_search_sends_the_key_saved_at_once(self, app: App) -> None:
        app.api.integrations.web_search.update(api_key="tvly-x")
        assert _key_a_search_sends(app) == "Bearer tvly-x"

    def test_the_next_launch_reads_the_key_back(self, app: App) -> None:
        app.api.integrations.web_search.update(api_key="tvly-x")
        relaunched = backend_launch.open_app(app.paths.root)
        try:
            assert Api(relaunched).integrations.web_search.get() == WebSearch(True)
        finally:
            relaunched.close()

    def test_an_empty_key_forgets_it(self, app: App) -> None:
        app.api.integrations.web_search.update(api_key="tvly-x")
        app.api.integrations.web_search.update(api_key="")
        assert app.api.integrations.web_search.get() == WebSearch(False)
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert file["web_search"]["api_key"] == ""
        # forgetting none is a no-op, and says so
        app.api.integrations.web_search.update(api_key="  ")
        assert app.api.integrations.web_search.get() == WebSearch(False)

    def test_a_section_a_hand_edit_dropped_is_founded_by_the_save(self, app: App) -> None:
        app.paths.integrations_file.write_text("# emptied by hand\n")
        app.api.integrations.web_search.update(api_key="tvly-x")
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert _unsealed(app, file["web_search"]["api_key"]) == "tvly-x"


class TestSupport:
    def test_a_request_is_posted_with_its_address_and_the_app_named(self, app: App) -> None:
        app.api.integrations.support_requests.create(
            body="  The canvas froze.  ", email=" me@example.com "
        )
        assert app.server.posts[-1] == DESK
        assert app.server.requests[-1] == {
            "message": "The canvas froze.",
            "email": "me@example.com",
        }
        assert app.server.request_headers[-1]["User-Agent"] == f"otaku/{__version__}"

    def test_without_an_address_none_is_sent(self, app: App) -> None:
        app.api.integrations.support_requests.create(body="Thanks for the canvas.")
        assert app.server.requests[-1] == {"message": "Thanks for the canvas."}

    def test_what_the_desk_would_drop_is_refused_before_anything_leaves(self, app: App) -> None:
        for body, email in (
            ("", ""),
            ("   \n", ""),
            ("x" * (SupportDesk.max_message + 1), ""),
            ("Hi", "me@localhost"),
            ("Hi", "me"),
            ("Hi", "a b@c.de"),
            ("Hi", "x" * 195 + "@a.com"),
        ):
            with pytest.raises(Refused):
                app.api.integrations.support_requests.create(body=body, email=email)
        assert DESK not in app.server.posts
        # at the limit, it goes
        app.api.integrations.support_requests.create(body="x" * SupportDesk.max_message)
        assert app.server.posts[-1] == DESK

    def test_a_desk_that_fails_is_a_refusal(self, app: App) -> None:
        app.server.support_status = 500
        with pytest.raises(Refused):
            app.api.integrations.support_requests.create(body="Hi")

    def test_the_command_sends_the_line_whole(self, app: App) -> None:
        app.play("/support The canvas froze — me@example.com")
        assert app.server.posts[-1] == DESK
        assert app.server.requests[-1] == {"message": "The canvas froze — me@example.com"}


@pytest.fixture(autouse=True)
def _services_at_the_scripted_server(server: ModelServer, monkeypatch: pytest.MonkeyPatch) -> None:
    """The support desk and the search engine at the scripted server's
    door, never the internet — set before the app binds them."""
    door = server.url.removesuffix("/v1")
    monkeypatch.setattr(SupportDesk, "endpoint", door + DESK)
    monkeypatch.setattr(web_search, "_TAVILY_ENDPOINT", door + SEARCH)


def _unsealed(app: App, value: str) -> str:
    return unseal(value, key_file=app.paths.config_key_file, service=app.paths.keychain_service)


def _key_a_search_sends(app: App) -> str:
    """The credential the engine is asked with when the narrator calls
    for a search: a story with the tool on, a line played."""
    app.api.stories.settings.update(app.ensure_story(), "allow_web_search", enabled=True)
    app.server.script = _after_the_tool(
        "Nothing to find.", first="```otk-web-search\ntides\n```", marker="Web search"
    )
    app.play("Look it up.")
    asked = app.server.posts.index(SEARCH)
    return app.server.request_headers[asked]["Authorization"]
