"""The registry: one client per configured section, the engine chosen by
the section's name, and the names no engine serves set aside."""

import pytest

from otaku.providers import ALL_CLIENTS, ProviderConfig, Registry
from otaku.providers.clients.generic import GenericClient
from otaku.providers.clients.ollama import OllamaClient

OLLAMA = ProviderConfig(name="ollama", url="http://localhost:11434/v1")
GENERIC = ProviderConfig(name="generic", url="http://localhost:8080/v1")
MINE = ProviderConfig(name="mine", url="http://localhost:9/v1")


class TestTheRoster:
    def test_the_eight_engines_in_the_panels_order(self) -> None:
        assert list(ALL_CLIENTS) == [
            "generic",
            "llamacpp",
            "koboldcpp",
            "ollama",
            "omlx",
            "lmstudio",
            "openrouter",
            "nanogpt",
        ]

    def test_every_client_answers_to_its_own_id(self) -> None:
        assert all(cls.id == name for name, cls in ALL_CLIENTS.items())


class TestSections:
    def test_a_section_is_served_by_the_engine_its_name_is(self) -> None:
        registry = Registry({"ollama": OLLAMA, "generic": GENERIC})
        assert isinstance(registry.get("ollama"), OllamaClient)
        assert isinstance(registry.get("generic"), GenericClient)

    def test_the_ids_come_sorted(self) -> None:
        assert Registry({"ollama": OLLAMA, "generic": GENERIC}).list() == ["generic", "ollama"]

    def test_a_section_under_any_other_name_is_set_aside(self) -> None:
        registry = Registry({"ollama": OLLAMA, "mine": MINE, "zbox": MINE})
        assert registry.ignored == ("mine", "zbox")
        assert registry.list() == ["ollama"]
        assert registry.get("mine") is None

    def test_an_unconfigured_engine_has_no_client(self) -> None:
        assert Registry({}).get("ollama") is None

    def test_a_client_is_built_once(self) -> None:
        registry = Registry({"generic": GENERIC})
        assert registry.get("generic") is registry.get("generic")


class TestUpdate:
    def test_a_new_configuration_rebuilds_the_client(self) -> None:
        registry = Registry({"generic": GENERIC})
        before = registry.get("generic")
        moved = ProviderConfig(name="generic", url="http://localhost:9090/v1")
        registry.update(moved)
        after = registry.get("generic")
        assert after is not before
        assert after is not None and after.config.url == moved.url
        assert registry.configs["generic"] == moved

    def test_a_section_may_be_founded_by_an_update(self) -> None:
        registry = Registry({})
        registry.update(OLLAMA)
        assert registry.list() == ["ollama"]
        assert isinstance(registry.get("ollama"), OllamaClient)

    def test_a_name_no_engine_answers_to_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Registry({}).update(MINE)


class TestMap:
    def test_runs_over_every_configured_provider_in_order(self) -> None:
        registry = Registry({"ollama": OLLAMA, "generic": GENERIC})
        assert registry.map(str.upper) == ["OLLAMA", "GENERIC"]

    def test_runs_over_the_ids_given_in_their_order(self) -> None:
        registry = Registry({"ollama": OLLAMA, "generic": GENERIC})
        assert registry.map(str.upper, ["generic"]) == ["GENERIC"]

    def test_nothing_asked_is_nothing_answered(self) -> None:
        assert Registry({}).map(str.upper) == []
