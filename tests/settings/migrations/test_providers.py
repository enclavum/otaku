"""The provider moves — the pure halves.

The contract: [providers.*] sections leave config.toml as they are —
comments included, headers unprefixed — while a section providers.toml
already holds is dropped instead: the new home wins. Separately, every
plain, non-empty api_key in providers.toml is sealed, wherever it came
from; a seal that cannot happen leaves the line for the next launch.
Everything else survives byte for byte, and unparseable output is
refused wholesale.
"""

from otaku.settings.migrations.providers import move_providers_text, seal_api_keys

BASE = (
    "[settings]\n"
    "show_banner = true            # the session header\n"
    "\n"
    "[context]\n"
    "head_messages = 20\n"
)


class TestMoveProvidersText:
    def test_moves_the_sections_as_they_are_and_unprefixes_the_headers(self) -> None:
        text = (
            "[settings]\nshow_banner = true\n\n"
            "# my main endpoint\n[providers.mine]\n"
            'url = "http://x:1/v1"   # the box\napi_key = ""\n\n'
            '[providers.other]\nurl = "http://y:2/v1"\n\n'
            "[context]\nhead_messages = 20\n"
        )
        result = move_providers_text(text, set())
        assert result is not None
        remaining, moved = result
        assert remaining == "[settings]\nshow_banner = true\n\n[context]\nhead_messages = 20\n"
        assert moved == (
            "# my main endpoint\n[mine]\n"
            'url = "http://x:1/v1"   # the box\napi_key = ""\n\n'
            '[other]\nurl = "http://y:2/v1"\n'
        )

    def test_everything_rides_verbatim(self) -> None:
        # The move does not clean: the retired knob and a plain key ride
        # as they are — the providers table sweeps them at their new home.
        text = '[providers.mine]\nurl = "x"\napi_key = "plain"\nsupports_thinking = true\n'
        result = move_providers_text(text, set())
        assert result == ("", '[mine]\nurl = "x"\napi_key = "plain"\nsupports_thinking = true\n')

    def test_a_taken_section_is_dropped_not_moved(self) -> None:
        text = '[providers.mine]\nurl = "old"\n\n[settings]\nshow_banner = true\n'
        result = move_providers_text(text, {"mine"})
        assert result == ("[settings]\nshow_banner = true\n", "")

    def test_no_provider_sections_means_none(self) -> None:
        assert move_providers_text(BASE, set()) is None


class TestSealApiKeys:
    def test_seals_every_plain_key(self) -> None:
        text = '[a]\napi_key = "plain-1"\n\n[b]\nurl = "y"\napi_key = "plain-2"\n'
        migrated = seal_api_keys(lambda v: f"sealed:{v}")(text)
        assert migrated == (
            '[a]\napi_key = "sealed:plain-1"\n\n[b]\nurl = "y"\napi_key = "sealed:plain-2"\n'
        )

    def test_sealed_and_empty_keys_stay(self) -> None:
        text = '[a]\napi_key = "sealed:abc"\n\n[b]\napi_key = ""\n'
        assert seal_api_keys(lambda v: "BOOM")(text) is text

    def test_a_seal_that_cannot_happen_leaves_the_line_for_next_time(self) -> None:
        text = '[a]\napi_key = "plain"\n'
        assert seal_api_keys(lambda v: v)(text) is text
