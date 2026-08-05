"""The config surface's pure parts.

`serves` answers whether a "provider/model" spec names a configured
provider; `to_toml` renders a configuration as the config.toml text, which
above all must parse back as TOML with every section present.
"""

import tomllib

from otaku.settings.config import Config, ProviderConfig, providers_toml


class TestServes:
    def test_a_configured_provider_with_a_model(self) -> None:
        assert config().serves("omlx/some-model") is True

    def test_an_unknown_provider(self) -> None:
        assert config().serves("nope/some-model") is False

    def test_a_spec_without_a_model(self) -> None:
        assert config().serves("omlx") is False
        assert config().serves("") is False


class TestToToml:
    def test_renders_valid_toml_without_providers(self) -> None:
        parsed = tomllib.loads(config().to_toml())
        assert "providers" not in parsed  # they live in providers.toml

    def test_every_section_is_present(self) -> None:
        parsed = tomllib.loads(config().to_toml())
        sections = ("settings", "ui", "context", "lore_extraction", "database", "encryption")
        for section in sections:
            assert section in parsed, section

    def test_values_roundtrip(self) -> None:
        parsed = tomllib.loads(config().to_toml())
        assert parsed["lore_extraction"]["enabled"] is True
        assert parsed["lore_extraction"]["scene_min_chars"] == 6000
        assert parsed["ui"]["dialogue_color"] == "auto"
        assert parsed["ui"]["dialogue_bold"] is False


class TestProvidersToml:
    def test_one_top_level_section_per_provider(self) -> None:
        parsed = tomllib.loads(providers_toml(config().providers))
        assert set(parsed) == {"omlx", "ollama"}
        assert parsed["omlx"]["url"] == "http://localhost:8100/v1"
        assert parsed["omlx"]["api_key"] == "k"

    def test_keep_alive_appears_only_when_set(self) -> None:
        with_it = providers_toml({"a": ProviderConfig(name="a", url="x", keep_alive="24h")})
        assert 'keep_alive = "24h"' in with_it
        assert "keep_alive" not in providers_toml({"a": ProviderConfig(name="a", url="x")})


def config() -> Config:
    return Config(
        providers={
            "omlx": ProviderConfig(name="omlx", url="http://localhost:8100/v1", api_key="k"),
            "ollama": ProviderConfig(name="ollama", url="http://localhost:11434/v1"),
        }
    )
