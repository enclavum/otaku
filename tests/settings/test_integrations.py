"""The integration sections' pure part: `render` writes integrations.toml
— one top-level [name] section per integration, then the one CHOICE the
file holds besides them, `[web_search] engine` — round-trip exact, and
the two blocks a launch founds a missing section with."""

import tomllib

from otaku.settings.integrations import (
    ENGINE_KEY,
    WEB_SEARCH_SECTION,
    IntegrationConfig,
    render,
    section_block,
    web_search_block,
)


class TestRender:
    def test_one_top_level_section_per_integration_then_the_choice(self) -> None:
        configs = {
            "tavily": IntegrationConfig("tavily", "https://api.tavily.com/search", "sealed:k"),
            "exa": IntegrationConfig("exa", "https://api.exa.ai/search"),
        }
        parsed = tomllib.loads(render(configs, "tavily"))
        assert set(parsed) == {"tavily", "exa", WEB_SEARCH_SECTION}
        assert parsed["tavily"] == {"url": "https://api.tavily.com/search", "api_key": "sealed:k"}
        assert parsed["exa"] == {"url": "https://api.exa.ai/search", "api_key": ""}
        assert parsed[WEB_SEARCH_SECTION] == {ENGINE_KEY: "tavily"}

    def test_no_choice_is_an_empty_word_not_an_absent_row(self) -> None:
        # the row is where the choice goes, so the file shows it
        parsed = tomllib.loads(render({}))
        assert parsed == {WEB_SEARCH_SECTION: {ENGINE_KEY: ""}}

    def test_the_blocks_are_what_the_file_renders(self) -> None:
        config = IntegrationConfig("exa", "https://api.exa.ai/search", "sealed:k")
        assert section_block(config) == (
            '[exa]\nurl = "https://api.exa.ai/search"\napi_key = "sealed:k"'
        )
        assert web_search_block("exa") == f'[{WEB_SEARCH_SECTION}]\n{ENGINE_KEY} = "exa"'
        # a founded section carries no key, and the whole file is made of these blocks
        whole = render({"exa": config}, "exa")
        assert section_block(config) in whole and web_search_block("exa") in whole

    def test_a_value_with_a_quote_survives_the_round_trip(self) -> None:
        config = IntegrationConfig("exa", 'https://x/"q"', 'k"1')
        parsed = tomllib.loads(render({"exa": config}, 'e"x'))
        assert parsed["exa"] == {"url": 'https://x/"q"', "api_key": 'k"1'}
        assert parsed[WEB_SEARCH_SECTION][ENGINE_KEY] == 'e"x'
