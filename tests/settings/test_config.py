"""The config surface's pure parts.

`serves` answers whether a "provider/model" spec names a configured
provider; `to_toml` renders a configuration as the config.toml text, which
above all must parse back as TOML with every section present.
"""

import tomllib

from otaku.settings.config import Config, Provider


class TestServes:
    def test_a_configured_provider_with_a_model(self) -> None:
        assert config().serves("omlx/some-model") is True

    def test_an_unknown_provider(self) -> None:
        assert config().serves("nope/some-model") is False

    def test_a_spec_without_a_model(self) -> None:
        assert config().serves("omlx") is False
        assert config().serves("") is False


class TestToToml:
    def test_renders_valid_toml(self) -> None:
        parsed = tomllib.loads(config().to_toml())
        assert set(parsed["providers"]) == {"omlx", "ollama"}

    def test_every_section_is_present(self) -> None:
        parsed = tomllib.loads(config().to_toml())
        for section in ("settings", "context", "lore_extraction", "database", "encryption"):
            assert section in parsed, section

    def test_values_roundtrip(self) -> None:
        parsed = tomllib.loads(config().to_toml())
        assert parsed["providers"]["omlx"]["url"] == "http://localhost:8100/v1"
        assert parsed["providers"]["omlx"]["api_key"] == "k"
        assert parsed["lore_extraction"]["enabled"] is True
        assert parsed["lore_extraction"]["scene_min_chars"] == 6000


def config() -> Config:
    return Config(
        providers={
            "omlx": Provider(name="omlx", url="http://localhost:8100/v1", api_key="k"),
            "ollama": Provider(name="ollama", url="http://localhost:11434/v1"),
        }
    )
