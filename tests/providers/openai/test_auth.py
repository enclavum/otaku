"""The key in force: the section's beats the shell's, clearing one
uncovers the other, and the header carries whichever it is."""

import pytest

from otaku.providers import KeySource, ProviderConfig
from otaku.providers.http import Http
from otaku.providers.openai.auth import OpenAIAuth

VARIABLE = "OTAKU_TEST_ENGINE_KEY"


def _auth(key: str = "") -> OpenAIAuth:
    return OpenAIAuth(
        ProviderConfig(name="engine", url="http://127.0.0.1:9/v1", api_key=key), VARIABLE
    )


class TestTheKeyInForce:
    def test_the_sections_key_is_in_force_and_says_where_it_came_from(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(VARIABLE, raising=False)
        auth = _auth("typed")
        assert (auth.api_key, auth.key_source) == ("typed", KeySource.CONFIG)

    def test_the_variable_stands_in_when_the_section_has_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(VARIABLE, "from-env")
        auth = _auth()
        assert (auth.api_key, auth.key_source) == ("from-env", KeySource.ENV)

    def test_the_sections_key_beats_the_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(VARIABLE, "from-env")
        assert _auth("typed").key_source is KeySource.CONFIG

    def test_no_key_anywhere_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(VARIABLE, raising=False)
        auth = _auth()
        assert (auth.api_key, auth.key_source) == ("", None)

    def test_an_engine_without_a_variable_reads_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(VARIABLE, "from-env")
        keyless = OpenAIAuth(ProviderConfig(name="engine", url="http://127.0.0.1:9/v1"), "")
        assert keyless.api_key == ""


class TestHeaders:
    def test_the_key_rides_as_a_bearer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(VARIABLE, raising=False)
        assert _auth("typed").headers == {"Authorization": "Bearer typed"}

    def test_no_key_sends_no_authorization(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(VARIABLE, raising=False)
        assert "Authorization" not in _auth().headers


class TestVerification:
    def test_the_base_has_no_account_to_ask_and_verifies_nothing(self) -> None:
        # Nothing listens on the port: a check that asked would fail.
        _auth("typed").verify_key(Http("engine", {}).within(0.5, "listing"))
