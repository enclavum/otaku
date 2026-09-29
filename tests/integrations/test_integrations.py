"""The integrations, where the answer is pure.

A section of integrations.toml IS its integration: `Integrations.from_settings`
binds each configured section the registry knows to an instance holding
its url and key, passes the rest over as `ignored`, and resolves the
file's one choice — which engine web search asks — to its bound engine,
or reports the word it could not resolve. `search_engine_of_key` tells
an engine from a key's own shape: the one whose prefix it starts with,
else the one whose keys carry no mark. Each engine declares its fixed
endpoint, which `default_sections` founds a keyless section on. The
transport's `results_of` reads an engine's answer and refuses one
without results.
"""

import re

import pytest

from otaku.integrations import (
    ALL_INTEGRATIONS,
    Integration,
    IntegrationConfig,
    IntegrationError,
    Integrations,
    SearchEngine,
    default_sections,
    search_engine_of_key,
)
from otaku.integrations.base import results_of
from otaku.integrations.exa import Exa
from otaku.integrations.tavily import Tavily
from otaku.settings.integrations import IntegrationSettings


class TestRegistry:
    def test_every_integration_is_registered_under_its_own_id(self) -> None:
        # the package's own classes: a test's stand-in engine is no integration
        for cls in every_integration(Integration):
            if cls.id and cls.__module__.startswith("otaku.integrations"):
                assert ALL_INTEGRATIONS.get(cls.id) is cls, cls.__name__

    def test_an_id_is_one_lowercase_word(self) -> None:
        for name in ALL_INTEGRATIONS:
            assert re.fullmatch(r"[a-z]+", name), name

    def test_the_two_search_engines_and_their_endpoints(self) -> None:
        assert set(ALL_INTEGRATIONS) == {"tavily", "exa"}
        assert Tavily.endpoint.startswith("https://") and Exa.endpoint.startswith("https://")
        assert (Tavily.label, Exa.label) == ("Tavily", "Exa")
        for cls in ALL_INTEGRATIONS.values():
            assert issubclass(cls, SearchEngine), cls.__name__

    def test_a_default_section_is_the_endpoint_with_no_key(self) -> None:
        assert default_sections() == {
            "tavily": IntegrationConfig("tavily", Tavily.endpoint),
            "exa": IntegrationConfig("exa", Exa.endpoint),
        }
        assert all(not config.api_key for config in default_sections().values())


class TestBinding:
    def test_an_instance_holds_its_sections_url_and_key(self) -> None:
        bound = Tavily(IntegrationConfig("tavily", "https://proxy.test/search", "tvly-1"))
        assert (bound.url, bound.api_key) == ("https://proxy.test/search", "tvly-1")
        # a section with no url in force falls back to the fixed endpoint
        assert Exa(IntegrationConfig("exa", "", "k")).url == Exa.endpoint

    def test_the_settings_bind_to_one_value(self) -> None:
        settings = IntegrationSettings(
            {
                "tavily": IntegrationConfig("tavily", Tavily.endpoint, "tvly-1"),
                "exa": IntegrationConfig("exa", Exa.endpoint),
            },
            web_search_engine="tavily",
        )
        found = Integrations.from_settings(settings)
        assert set(found.bound) == {"tavily", "exa"}
        assert isinstance(found.bound["tavily"], Tavily)
        assert found.web_search_engine is found.bound["tavily"]
        assert found.ignored == () and found.unknown_web_search_engine == ""

    def test_a_section_no_integration_answers_to_is_reported_not_dropped_in_silence(self) -> None:
        settings = IntegrationSettings(
            {"bing": IntegrationConfig("bing", "https://x"), "exa": IntegrationConfig("exa", "u")}
        )
        found = Integrations.from_settings(settings)
        assert found.ignored == ("bing",)
        assert set(found.bound) == {"exa"}

    def test_no_choice_means_no_engine_and_a_bad_choice_is_reported_as_written(self) -> None:
        sections = {"exa": IntegrationConfig("exa", "u", "k")}
        none = Integrations.from_settings(IntegrationSettings(sections))
        assert none.web_search_engine is None and none.unknown_web_search_engine == ""
        wrong = Integrations.from_settings(IntegrationSettings(sections, web_search_engine="bing"))
        assert wrong.web_search_engine is None and wrong.unknown_web_search_engine == "bing"
        # a choice naming a section the file lacks is a choice of nothing bound
        unbound = Integrations.from_settings(
            IntegrationSettings(sections, web_search_engine="tavily")
        )
        assert unbound.web_search_engine is None
        assert unbound.unknown_web_search_engine == "tavily"

    def test_nothing_configured_binds_nothing(self) -> None:
        found = Integrations.from_settings(IntegrationSettings())
        assert (dict(found.bound), found.web_search_engine, found.ignored) == ({}, None, ())
        assert Integrations() == found


class TestSearchEngineOfKey:
    def test_a_key_carrying_tavilys_mark_is_tavilys(self) -> None:
        assert Tavily.key_prefix == "tvly"
        assert search_engine_of_key("tvly-dev-abc") is Tavily
        assert search_engine_of_key("tvly") is Tavily

    def test_any_other_key_is_the_engine_whose_keys_carry_no_mark(self) -> None:
        assert Exa.key_prefix == ""
        for key in ("0f3a-2b", "exa-xyz", "TVLY-uppercase-is-no-mark", "k"):
            assert search_engine_of_key(key) is Exa, key


class TestResultsOf:
    def test_the_results_list_is_read_dicts_alone(self) -> None:
        data = {"results": [{"title": "A"}, "junk", {"title": "B"}]}
        assert results_of(data) == [{"title": "A"}, {"title": "B"}]
        assert results_of({"results": []}) == []

    def test_an_answer_without_results_is_refused(self) -> None:
        for data in ({}, {"results": "none"}, [], None, "text"):
            with pytest.raises(IntegrationError):
                results_of(data)


def every_integration(base: type[Integration]) -> list[type[Integration]]:
    """Every class below `base`, however deep."""
    found: list[type[Integration]] = []
    for sub in base.__subclasses__():
        found += [sub, *every_integration(sub)]
    return found
