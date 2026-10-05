"""The integrations, where the answer is pure.

Every integration is in the one registry, under its id — its section's
name; `default_sections` founds a keyless section for each one a section
configures, and none for those that are not configurable (the sky's
facts, the support desk). `Integrations.bind` binds EVERY integration:
a configurable one to its section in the file, or to the one it is
founded with where the file has none; a section no configurable
integration is named for is passed over. `get` answers one by its class.

Web search is one integration under ONE key: `search` asks no engine
without a key, and asks the engine the key belongs to — Tavily's keys
start with "tvly", any other is Exa's — refusing an answer without
results. The transport is stubbed: nothing leaves the machine.
"""

import re
from typing import Any

import pytest

from otaku.integrations import (
    ALL_INTEGRATIONS,
    Astronomy,
    Integration,
    IntegrationConfig,
    IntegrationError,
    Integrations,
    SearchResult,
    SupportDesk,
    WebSearch,
    default_sections,
)
from otaku.integrations import web_search as web_search_module
from otaku.settings.integrations import IntegrationSettings


class TestRegistry:
    def test_every_integration_is_registered_under_its_own_id(self) -> None:
        for cls in every_integration(Integration):
            if cls.__module__.startswith("otaku.integrations"):
                assert cls in ALL_INTEGRATIONS, cls.__name__
        assert len({cls.id for cls in ALL_INTEGRATIONS}) == len(ALL_INTEGRATIONS)

    def test_an_id_is_a_section_name(self) -> None:
        for cls in ALL_INTEGRATIONS:
            assert re.fullmatch(r"[a-z]+(_[a-z]+)*", cls.id), cls.id

    def test_only_web_search_is_configured_by_a_section(self) -> None:
        assert {cls.id for cls in ALL_INTEGRATIONS if cls.configurable} == {"web_search"}
        assert not Astronomy.configurable and not SupportDesk.configurable

    def test_a_default_section_has_no_key(self) -> None:
        assert set(default_sections()) == {"web_search"}
        assert all(not config.api_key for config in default_sections().values())


class TestBinding:
    def test_an_instance_holds_its_sections_url_and_key(self) -> None:
        bound = SupportDesk(IntegrationConfig("support", "https://proxy.test/support", "k"))
        assert (bound.url, bound.api_key) == ("https://proxy.test/support", "k")
        # a section with no url in force falls back to the fixed endpoint
        assert SupportDesk(IntegrationConfig("support", "")).url == SupportDesk.endpoint

    def test_every_integration_is_bound_whatever_the_file_holds(self) -> None:
        found = Integrations.bind(IntegrationSettings())
        assert set(found.by_id) == {cls.id for cls in ALL_INTEGRATIONS}
        assert found.get(WebSearch).api_key == ""
        assert isinstance(found.get(Astronomy), Astronomy)

    def test_a_section_binds_its_integration(self) -> None:
        settings = IntegrationSettings(
            {"web_search": IntegrationConfig("web_search", "", "tvly-1")}
        )
        assert Integrations.bind(settings).get(WebSearch).api_key == "tvly-1"

    def test_a_section_no_configurable_integration_is_named_for_is_passed_over(self) -> None:
        settings = IntegrationSettings(
            {
                "bing": IntegrationConfig("bing", "https://x", "k"),
                "tavily": IntegrationConfig("tavily", "https://y", "tvly-old"),
            }
        )
        found = Integrations.bind(settings)
        assert "bing" not in found.by_id and "tavily" not in found.by_id
        assert found.get(WebSearch).api_key == ""

    def test_get_answers_by_class(self) -> None:
        found = Integrations.bind(IntegrationSettings())
        for cls in ALL_INTEGRATIONS:
            assert isinstance(found.get(cls), cls)


class TestWebSearch:
    def test_no_key_asks_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        asked = stub_transport(monkeypatch, {"results": []})
        with pytest.raises(IntegrationError):
            web_search("").search("river", max_results=3)
        assert asked == []

    def test_a_key_with_tavilys_mark_asks_tavily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        asked = stub_transport(monkeypatch, {"results": [{"title": "T", "content": "snip"}]})
        found = web_search("tvly-dev-abc").search("river", max_results=3)
        assert found == [SearchResult("T", "snip")]
        url, headers, body = asked[0]
        assert "tavily" in url and headers == {"Authorization": "Bearer tvly-dev-abc"}
        assert (body["query"], body["max_results"]) == ("river", 3)

    def test_any_other_key_asks_exa(self, monkeypatch: pytest.MonkeyPatch) -> None:
        answer = {"results": [{"title": "E", "highlights": ["one", "two"]}]}
        for key in ("0f3a-2b", "exa-xyz", "TVLY-uppercase-is-no-mark"):
            asked = stub_transport(monkeypatch, answer)
            found = web_search(key).search("river", max_results=2)
            assert found == [SearchResult("E", "one … two")], key
            url, headers, body = asked[0]
            assert "exa" in url and headers == {"x-api-key": key}, key
            assert body["numResults"] == 2

    def test_an_answer_without_results_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for data in ({}, {"results": "none"}, [], None, "text"):
            stub_transport(monkeypatch, data)
            with pytest.raises(IntegrationError):
                web_search("tvly-1").search("river", max_results=1)

    def test_results_are_read_dicts_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_transport(monkeypatch, {"results": [{"title": "A", "content": "a"}, "junk"]})
        assert web_search("tvly-1").search("q", max_results=2) == [SearchResult("A", "a")]


def web_search(key: str) -> WebSearch:
    """Web search bound to `key`."""
    return WebSearch(IntegrationConfig("web_search", "", key))


def stub_transport(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[tuple[str, Any, Any]]:
    """The transport answering `answer` to every request, each recorded."""
    asked: list[tuple[str, Any, Any]] = []

    def post_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        asked.append((url, headers, body))
        return answer

    monkeypatch.setattr(web_search_module, "post_json", post_json)
    return asked


def every_integration(base: type[Integration]) -> list[type[Integration]]:
    """Every class below `base`, however deep."""
    found: list[type[Integration]] = []
    for sub in base.__subclasses__():
        found += [sub, *every_integration(sub)]
    return found
