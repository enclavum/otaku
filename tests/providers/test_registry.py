"""The registry's promises that need no server.

A section's name IS its engine: it selects the client, and a section
under any other name is not served — left out of the configs, listed
in `ignored` for the launch to say so. An unconfigured name is a
ValueError. A client is built once and kept until `update` swaps its
section, which drops it so the next request is built against the new
url and key. `names` are the served providers sorted; `map` runs a
function over every served provider, or the names given, results in
that order.
"""

import pytest

from otaku.providers import CLIENTS, ProviderConfig, Registry
from otaku.providers.clients.generic import GenericClient
from otaku.providers.clients.ollama import OllamaClient

OLLAMA = ProviderConfig(name="ollama", url="http://ollama/v1")
GENERIC = ProviderConfig(name="generic", url="http://mine/v1", api_key="k")
MINE = ProviderConfig(name="mine", url="http://mine/v1", api_key="k")


class TestLookup:
    def test_the_name_selects_the_engine(self) -> None:
        registry = Registry({"ollama": OLLAMA, "generic": GENERIC})
        assert type(registry.get_client("ollama")) is OllamaClient
        assert type(registry.get_client("generic")) is GenericClient
        assert registry.get_client("generic").config is GENERIC

    def test_every_shipped_engine_is_selected_by_its_kind(self) -> None:
        registry = Registry(
            {kind: ProviderConfig(name=kind, url="http://x/v1") for kind in CLIENTS}
        )
        for kind, cls in CLIENTS.items():
            assert type(registry.get_client(kind)) is cls

    def test_a_section_under_any_other_name_is_left_out_and_named(self) -> None:
        registry = Registry({"ollama": OLLAMA, "mine": MINE, "zbox": MINE})
        assert registry.names() == ["ollama"]
        assert registry.configs == {"ollama": OLLAMA}
        assert registry.ignored == ("mine", "zbox")
        with pytest.raises(ValueError):
            registry.get_client("mine")

    def test_an_unconfigured_name_is_a_value_error(self) -> None:
        with pytest.raises(ValueError):
            Registry({}).get_client("ollama")

    def test_a_client_is_built_once(self) -> None:
        registry = Registry({"generic": GENERIC})
        assert registry.get_client("generic") is registry.get_client("generic")

    def test_names_are_sorted_and_configs_are_the_sections(self) -> None:
        registry = Registry({"ollama": OLLAMA, "generic": GENERIC})
        assert registry.names() == ["generic", "ollama"]
        assert registry.configs == {"ollama": OLLAMA, "generic": GENERIC}
        assert registry.ignored == ()


class TestUpdate:
    def test_an_update_swaps_the_section_and_rebuilds_the_client(self) -> None:
        registry = Registry({"generic": GENERIC})
        before = registry.get_client("generic")
        moved = ProviderConfig(name="generic", url="http://elsewhere/v1")
        registry.update(moved)
        after = registry.get_client("generic")
        assert after is not before
        assert after.config is moved
        assert registry.configs["generic"] is moved

    def test_an_update_can_found_a_section(self) -> None:
        registry = Registry({})
        registry.update(OLLAMA)
        assert registry.names() == ["ollama"]
        assert type(registry.get_client("ollama")) is OllamaClient


class TestMap:
    def test_every_served_provider_in_configuration_order(self) -> None:
        registry = Registry({"ollama": OLLAMA, "generic": GENERIC, "mine": MINE})
        assert registry.map(lambda name, config: (name, config.url)) == [
            ("ollama", "http://ollama/v1"),
            ("generic", "http://mine/v1"),
        ]

    def test_the_names_given_in_their_order(self) -> None:
        registry = Registry({"ollama": OLLAMA, "generic": GENERIC})
        assert registry.map(lambda name, config: name, ["generic"]) == ["generic"]
        assert registry.map(lambda name, config: name, ["generic", "ollama"]) == [
            "generic",
            "ollama",
        ]
        assert registry.map(lambda name, config: name, []) == []

    def test_an_error_in_the_function_propagates(self) -> None:
        registry = Registry({"generic": GENERIC})

        def fail(name: str, config: ProviderConfig) -> None:
            raise RuntimeError(name)

        with pytest.raises(RuntimeError):
            registry.map(fail)
