"""The config surface's pure parts: `to_toml` renders a configuration
as the config.toml text, which above all must parse back as TOML with
every section present. (The old `serves` check moved to the launch —
scenario territory now.)"""

import tomllib

from otaku.settings.config import Config


class TestToToml:
    def test_renders_valid_toml_without_providers(self) -> None:
        parsed = tomllib.loads(Config().to_toml())
        assert "providers" not in parsed  # they live in providers.toml

    def test_every_section_is_present(self) -> None:
        parsed = tomllib.loads(Config().to_toml())
        sections = ("settings", "terminal", "context", "lore_extraction", "database", "encryption")
        for section in sections:
            assert section in parsed, section

    def test_values_roundtrip(self) -> None:
        parsed = tomllib.loads(Config().to_toml())
        assert parsed["lore_extraction"]["enabled"] is True
        assert parsed["lore_extraction"]["scene_min_chars"] == 6000
        assert parsed["terminal"]["dialogue_color"] == "auto"
        assert parsed["terminal"]["dialogue_bold"] is False
        assert parsed["web"]["terminal_serve"] is False  # the chat serves nothing unasked
        assert parsed["settings"]["check_updates"] is True  # on until turned off
