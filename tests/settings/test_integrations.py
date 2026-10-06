"""The integration sections' pure part: `render` writes integrations.toml
— one top-level [name] section per integration — round-trip exact, and
`section_block` is the block a launch founds a missing section with: a
url row only where there is a url, and the key."""

import tomllib

from otaku.settings.integrations import IntegrationConfig, render, section_block


class TestRender:
    def test_one_top_level_section_per_integration(self) -> None:
        configs = {
            "web_search": IntegrationConfig("web_search", "", "sealed:k"),
            "proxy": IntegrationConfig("proxy", "https://x.test/api"),
        }
        parsed = tomllib.loads(render(configs))
        assert parsed == {
            "web_search": {"api_key": "sealed:k"},
            "proxy": {"url": "https://x.test/api", "api_key": ""},
        }

    def test_nothing_configured_renders_no_section(self) -> None:
        assert tomllib.loads(render({})) == {}

    def test_the_block_is_what_the_file_renders(self) -> None:
        keyless = IntegrationConfig("web_search", "")
        assert section_block(keyless) == '[web_search]\napi_key = ""'
        with_url = IntegrationConfig("proxy", "https://x.test/api", "sealed:k")
        assert (
            section_block(with_url) == '[proxy]\nurl = "https://x.test/api"\napi_key = "sealed:k"'
        )
        whole = render({"web_search": keyless, "proxy": with_url})
        assert section_block(keyless) in whole and section_block(with_url) in whole

    def test_a_value_with_a_quote_survives_the_round_trip(self) -> None:
        config = IntegrationConfig("proxy", 'https://x/"q"', 'k"1')
        assert tomllib.loads(render({"proxy": config}))["proxy"] == {
            "url": 'https://x/"q"',
            "api_key": 'k"1',
        }
