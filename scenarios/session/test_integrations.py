"""The web search key: ONE box in either frontend, no engine named. The
key's own shape says whose it is — a `tvly` mark is Tavily's, any other
key Exa's — and it goes sealed into that engine's section of
integrations.toml, the engine's id into the file's `[web_search]`
choice, both live in the session at once; "" forgets the chosen
engine's key and the choice; a section a hand edit dropped is founded
by the save. The value goes in and never comes back out through a
read: `web_search` says which engine is chosen and whether it holds a
key."""

import tomllib

import pytest

from otaku.backend import launch as backend_launch
from otaku.backend.api import integrations as api_integrations
from otaku.backend.api.integrations import WebSearch
from otaku.backend.session import Refused
from otaku.encryption import unseal
from scenarios.support.harness import App


class TestWebSearchKey:
    def test_nothing_stands_until_a_key_is_saved(self, app: App) -> None:
        assert api_integrations.web_search(app.session) == WebSearch("", "", False)
        assert app.session.integrations.web_search_engine is None
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert file["web_search"]["engine"] == ""
        assert file["tavily"]["api_key"] == "" and file["exa"]["api_key"] == ""

    def test_a_marked_key_is_tavilys_sealed_into_its_section_and_made_the_choice(
        self, app: App
    ) -> None:
        answer = api_integrations.set_web_search_key(app.session, " tvly-dev-123 ")
        assert answer == "Saved web search key for Tavily."
        assert api_integrations.web_search(app.session) == WebSearch("tavily", "Tavily", True)
        raw = app.paths.integrations_file.read_text()
        assert "tvly-dev-123" not in raw  # never plain in the file
        file = tomllib.loads(raw)
        assert _unsealed(app, file["tavily"]["api_key"]) == "tvly-dev-123"
        assert file["web_search"]["engine"] == "tavily"
        assert file["exa"]["api_key"] == ""
        # the session holds the plain key at once, on the bound engine a
        # search runs on
        engine = app.session.integrations.web_search_engine
        assert engine is not None and (engine.id, engine.api_key) == ("tavily", "tvly-dev-123")

    def test_any_other_key_is_exas(self, app: App) -> None:
        answer = api_integrations.set_web_search_key(app.session, "0f3a-2b")
        assert answer == "Saved web search key for Exa."
        assert api_integrations.web_search(app.session) == WebSearch("exa", "Exa", True)
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert file["web_search"]["engine"] == "exa"
        assert _unsealed(app, file["exa"]["api_key"]) == "0f3a-2b"
        assert file["tavily"]["api_key"] == ""

    def test_a_second_key_moves_the_choice(self, app: App) -> None:
        api_integrations.set_web_search_key(app.session, "tvly-x")
        api_integrations.set_web_search_key(app.session, "exa-key")
        assert api_integrations.web_search(app.session).engine == "exa"
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert file["web_search"]["engine"] == "exa"
        # the other engine's key stays in its section, unchosen
        assert _unsealed(app, file["tavily"]["api_key"]) == "tvly-x"

    def test_the_next_launch_reads_the_key_back(self, app: App) -> None:
        api_integrations.set_web_search_key(app.session, "tvly-x")
        relaunched = backend_launch.open_session(app.paths.root)
        try:
            engine = relaunched.integrations.web_search_engine
            assert engine is not None and (engine.id, engine.api_key) == ("tavily", "tvly-x")
        finally:
            relaunched.close()

    def test_an_empty_key_forgets_the_chosen_engines_key_and_the_choice(self, app: App) -> None:
        api_integrations.set_web_search_key(app.session, "tvly-x")
        answer = api_integrations.set_web_search_key(app.session, "")
        assert answer == "Cleared web search key for Tavily."
        assert api_integrations.web_search(app.session) == WebSearch("", "", False)
        assert app.session.integrations.web_search_engine is None
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert (file["tavily"]["api_key"], file["web_search"]["engine"]) == ("", "")
        with pytest.raises(Refused, match="No web search key to clear"):
            api_integrations.set_web_search_key(app.session, "  ")

    def test_a_section_a_hand_edit_dropped_is_founded_by_the_save(self, app: App) -> None:
        raw = app.paths.integrations_file.read_text()
        start = raw.index("[tavily]")
        end = raw.index("\n[", start + 1) + 1
        app.paths.integrations_file.write_text(raw[:start] + raw[end:])
        assert "[tavily]" not in app.paths.integrations_file.read_text()
        api_integrations.set_web_search_key(app.session, "tvly-x")
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert file["tavily"]["url"] == "https://api.tavily.com/search"
        assert _unsealed(app, file["tavily"]["api_key"]) == "tvly-x"
        assert file["web_search"]["engine"] == "tavily"


def _unsealed(app: App, value: str) -> str:
    return unseal(value, key_file=app.paths.config_key_file, service=app.paths.keychain_service)
