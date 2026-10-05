"""The web search key: ONE box in either frontend, and ONE key — Tavily's
or Exa's, which engine it belongs to told from its shape only when a
search is asked (`integrations.WebSearch`). It goes sealed into the web
search section of integrations.toml, which names no engine, and lives in
the session at once; "" forgets it; a section a hand edit dropped is
founded by the save. The value goes in and never comes back out through
a read: `web_search` says only whether a key is held."""

import tomllib

import pytest

from otaku.backend import launch as backend_launch
from otaku.backend.api import integrations as api_integrations
from otaku.backend.api.integrations import WebSearchState
from otaku.backend.session import Refused
from otaku.encryption import unseal
from otaku.integrations import WebSearch
from scenarios.support.harness import App


class TestWebSearchKey:
    def test_nothing_stands_until_a_key_is_saved(self, app: App) -> None:
        assert api_integrations.web_search(app.session) == WebSearchState(False)
        assert app.session.integrations.get(WebSearch).api_key == ""
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert file == {"web_search": {"api_key": ""}}

    def test_a_key_is_sealed_into_the_one_section_whichever_engines(self, app: App) -> None:
        for key in ("tvly-dev-123", "0f3a-2b"):
            api_integrations.set_web_search_key(app.session, f" {key} ")
            assert api_integrations.web_search(app.session) == WebSearchState(True)
            raw = app.paths.integrations_file.read_text()
            assert key not in raw  # never plain in the file
            file = tomllib.loads(raw)
            # one section, no engine named in it
            assert set(file) == {"web_search"} and set(file["web_search"]) == {"api_key"}
            assert _unsealed(app, file["web_search"]["api_key"]) == key
            # the session holds the plain key at once, where a search reads it
            assert app.session.integrations.get(WebSearch).api_key == key

    def test_the_next_launch_reads_the_key_back(self, app: App) -> None:
        api_integrations.set_web_search_key(app.session, "tvly-x")
        relaunched = backend_launch.open_session(app.paths.root)
        try:
            assert relaunched.integrations.get(WebSearch).api_key == "tvly-x"
        finally:
            relaunched.close()

    def test_an_empty_key_forgets_it(self, app: App) -> None:
        api_integrations.set_web_search_key(app.session, "tvly-x")
        api_integrations.set_web_search_key(app.session, "")
        assert api_integrations.web_search(app.session) == WebSearchState(False)
        assert app.session.integrations.get(WebSearch).api_key == ""
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert file["web_search"]["api_key"] == ""
        with pytest.raises(Refused):
            api_integrations.set_web_search_key(app.session, "  ")

    def test_a_section_a_hand_edit_dropped_is_founded_by_the_save(self, app: App) -> None:
        app.paths.integrations_file.write_text("# emptied by hand\n")
        api_integrations.set_web_search_key(app.session, "tvly-x")
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert _unsealed(app, file["web_search"]["api_key"]) == "tvly-x"


def _unsealed(app: App, value: str) -> str:
    return unseal(value, key_file=app.paths.config_key_file, service=app.paths.keychain_service)
