"""Config migrations — the pure text transforms.

The contract: each factory returns a text→text migration that edits
config.toml surgically — only the lines it must touch; every other byte,
comment, and blank survives. Applicability is decided on the PARSED file,
so a key mentioned in a comment or a string never false-matches, and a
commented-out `# key = …` counts as absent. Every migration is
idempotent. `apply_migrations` chains a table in order and returns the
original text — the same object — when nothing changed or when the
result no longer parses as TOML.
"""

from otaku.settings.migrations import (
    apply_migrations,
    drop_child_key,
    ensure_section,
    set_key,
    split_providers_text,
)

BASE = (
    "[settings]\n"
    "show_banner = true            # the session header\n"
    "\n"
    "[context]\n"
    "head_messages = 20\n"
)


class TestEnsureSection:
    def test_appends_a_missing_section_after_one_blank_line(self) -> None:
        migrated = ensure_section("display", "[display]\nsmooth = true")(BASE)
        assert migrated == BASE + "\n[display]\nsmooth = true\n"

    def test_leaves_a_present_section_alone(self) -> None:
        assert ensure_section("settings", "[settings]")(BASE) is BASE

    def test_a_mention_in_a_comment_is_not_presence(self) -> None:
        text = BASE + "# [display] will exist someday\n"
        migrated = ensure_section("display", "[display]\nsmooth = true")(text)
        assert "# [display] will exist someday" in migrated
        assert migrated.endswith("\n[display]\nsmooth = true\n")

    def test_after_places_the_section_below_its_anchor(self) -> None:
        migrated = ensure_section("display", "[display]\nsmooth = true", after="settings")(BASE)
        assert migrated == (
            "[settings]\n"
            "show_banner = true            # the session header\n"
            "\n"
            "[display]\n"
            "smooth = true\n"
            "\n"
            "[context]\n"
            "head_messages = 20\n"
        )

    def test_a_missing_anchor_falls_back_to_the_end(self) -> None:
        migrated = ensure_section("display", "[display]", after="vanished")(BASE)
        assert migrated == BASE + "\n[display]\n"


class TestSetKey:
    def test_replaces_the_value_and_drops_the_stale_trailing_comment(self) -> None:
        text = '[providers.test]\nurl = "http://old:1/v1"   # the old port\napi_key = ""\n'
        migrated = set_key("providers.test", "url", 'url = "http://new:2/v1"')(text)
        assert migrated == '[providers.test]\nurl = "http://new:2/v1"\napi_key = ""\n'

    def test_comment_lines_above_the_key_stay(self) -> None:
        text = "[settings]\n# how long the model idles\nidle = 5\n"
        migrated = set_key("settings", "idle", "idle = 30")(text)
        assert migrated == "[settings]\n# how long the model idles\nidle = 30\n"

    def test_an_absent_key_is_added_at_the_sections_end(self) -> None:
        text = '[providers.test]\nurl = "x"\n\n[settings]\nshow_banner = true\n'
        migrated = set_key("providers.test", "api_key", 'api_key = "sealed:abc"')(text)
        assert migrated == (
            '[providers.test]\nurl = "x"\napi_key = "sealed:abc"\n'
            "\n[settings]\nshow_banner = true\n"
        )

    def test_no_such_section_is_untouched(self) -> None:
        assert set_key("providers.gone", "url", 'url = "x"')(BASE) is BASE

    def test_the_same_line_is_untouched(self) -> None:
        text = "[settings]\nidle = 30\n"
        assert set_key("settings", "idle", "idle = 30")(text) is text


class TestDropChildKey:
    def test_removes_the_key_from_every_child_section(self) -> None:
        text = (
            "[providers.ollama]\n"
            'url = "http://localhost:11434/v1"\n'
            "supports_thinking = true\n"
            "\n"
            "[providers.kobold]\n"
            'url = "http://localhost:5001/v1"\n'
            "# flipped on while testing R1\n"
            "supports_thinking = false\n"
            "\n"
            "[settings]\n"
            "show_banner = true\n"
        )
        migrated = drop_child_key("providers", "supports_thinking")(text)
        assert migrated == (
            "[providers.ollama]\n"
            'url = "http://localhost:11434/v1"\n'
            "\n"
            "[providers.kobold]\n"
            'url = "http://localhost:5001/v1"\n'
            "\n"
            "[settings]\n"
            "show_banner = true\n"
        )

    def test_a_child_without_the_key_is_untouched(self) -> None:
        text = '[providers.a]\nsupports_thinking = true\n\n[providers.b]\nurl = "x"\n'
        migrated = drop_child_key("providers", "supports_thinking")(text)
        assert migrated == '[providers.a]\n\n[providers.b]\nurl = "x"\n'

    def test_absent_in_every_child_is_untouched(self) -> None:
        text = '[providers.test]\nurl = "http://localhost:1234/v1"\n'
        assert drop_child_key("providers", "supports_thinking")(text) is text

    def test_no_such_family_is_untouched(self) -> None:
        assert drop_child_key("providers", "supports_thinking")(BASE) is BASE


class TestSplitProvidersText:
    def test_moves_the_sections_as_they_are_and_unprefixes_the_headers(self) -> None:
        text = (
            "[settings]\nshow_banner = true\n\n"
            "# my main endpoint\n[providers.mine]\n"
            'url = "http://x:1/v1"   # the box\napi_key = ""\n\n'
            '[providers.other]\nurl = "http://y:2/v1"\n\n'
            "[context]\nhead_messages = 20\n"
        )
        result = split_providers_text(text, lambda v: "sealed:" + v)
        assert result is not None
        remaining, moved = result
        assert remaining == "[settings]\nshow_banner = true\n\n[context]\nhead_messages = 20\n"
        assert moved == (
            "# my main endpoint\n[mine]\n"
            'url = "http://x:1/v1"   # the box\napi_key = ""\n\n'
            '[other]\nurl = "http://y:2/v1"\n'
        )

    def test_drops_the_thinking_knob_and_seals_a_plain_key(self) -> None:
        text = '[providers.mine]\nurl = "x"\napi_key = "plain-secret"\nsupports_thinking = true\n'
        result = split_providers_text(text, lambda v: f"sealed:{v}")
        assert result == ("", '[mine]\nurl = "x"\napi_key = "sealed:plain-secret"\n')

    def test_a_sealed_or_empty_key_moves_untouched(self) -> None:
        text = (
            '[providers.a]\nurl = "x"\napi_key = "sealed:abc"\n\n'
            '[providers.b]\nurl = "y"\napi_key = ""\n'
        )
        result = split_providers_text(text, lambda v: "BOOM")
        assert result is not None
        _, moved = result
        assert 'api_key = "sealed:abc"' in moved
        assert 'api_key = ""' in moved
        assert "BOOM" not in moved

    def test_no_provider_sections_means_none(self) -> None:
        assert split_providers_text(BASE, lambda v: v) is None


class TestApplyMigrations:
    def test_chains_in_order(self) -> None:
        migrations = [
            ensure_section("display", "[display]"),
            set_key("display", "smooth", "smooth = true"),
        ]
        migrated = apply_migrations(BASE, migrations)
        assert migrated.endswith("\n[display]\nsmooth = true\n")

    def test_no_change_returns_the_original_object(self) -> None:
        migrations = [ensure_section("settings", "[settings]")]
        assert apply_migrations(BASE, migrations) is BASE

    def test_a_result_that_no_longer_parses_is_discarded(self) -> None:
        migrations = [lambda text: text + "\n[broken"]
        assert apply_migrations(BASE, migrations) is BASE
