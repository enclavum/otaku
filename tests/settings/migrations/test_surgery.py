"""The migration toolkit — the pure text transforms.

The contract: each factory returns a text→text migration that edits
config.toml surgically — only the lines it must touch; every other byte,
comment, and blank survives. Applicability is decided on the PARSED file,
so a key mentioned in a comment or a string never false-matches, and a
commented-out `# key = …` counts as absent. Every migration is
idempotent. `apply_migrations` chains a table in order and returns the
original text — the same object — when nothing changed or when the
result no longer parses as TOML.
"""

import tomllib

from otaku.settings.migrations.surgery import (
    apply_migrations,
    drop_key_everywhere,
    ensure_key,
    ensure_section,
    redacted,
    rename_section,
    set_key,
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


class TestEnsureKey:
    """A setting that arrived after the file was written: added so the
    surface stays discoverable, never imposed over what is there, and
    placed where the rendered file would have put it — `after` names the
    key it belongs behind, so a migrated file reads the same way down as
    a fresh one."""

    def test_no_neighbour_named_puts_it_under_the_header(self) -> None:
        text = '[terminal]\ndialogue_color = "auto"\ndialogue_bold = false\n'
        migrated = ensure_key("terminal", "theme", 'theme = "auto"')(text)
        assert migrated == (
            '[terminal]\ntheme = "auto"\ndialogue_color = "auto"\ndialogue_bold = false\n'
        )

    def test_a_named_neighbour_puts_it_directly_after(self) -> None:
        text = "[settings]\nshow_banner = true\nsmooth_streaming = true\n\n[context]\nhead = 20\n"
        migrated = ensure_key("settings", "sound", 'sound = "default"', after="show_banner")(text)
        assert migrated == (
            "[settings]\nshow_banner = true\n"
            'sound = "default"\nsmooth_streaming = true\n\n[context]\nhead = 20\n'
        )

    def test_a_neighbour_the_file_lacks_puts_it_at_the_sections_end(self) -> None:
        # Which is where a key rendered after an optional one belongs:
        # prompt_cache follows keep_alive, and a section without a
        # keep_alive still wants it last.
        text = "[settings]\nshow_banner = true\n\n[context]\nhead_messages = 20\n"
        migrated = ensure_key("settings", "sound", 'sound = "default"', after="gone")(text)
        assert migrated == (
            '[settings]\nshow_banner = true\nsound = "default"\n\n[context]\nhead_messages = 20\n'
        )

    def test_the_users_own_value_is_left_alone(self) -> None:
        # The difference from set_key: a value already chosen is the
        # user's, and every launch reruns this.
        text = '[settings]\nsound = "/my/bell.aiff"\n'
        assert ensure_key("settings", "sound", 'sound = "default"')(text) is text

    def test_a_commented_out_key_counts_as_absent(self) -> None:
        text = '[settings]\n# sound = "off"\nshow_banner = true\n'
        migrated = ensure_key("settings", "sound", 'sound = "default"', after="show_banner")(text)
        assert migrated.endswith('show_banner = true\nsound = "default"\n')

    def test_no_such_section_is_untouched(self) -> None:
        assert ensure_key("nowhere", "sound", 'sound = "default"')(BASE) is BASE


class TestRenameSection:
    """A section that has been called something else, whose contents
    never changed: only the header line is rewritten."""

    def test_renames_the_header_and_keeps_everything_under_it(self) -> None:
        text = (
            "[settings]\nshow_banner = true\n"
            '\n[ui]\n# mine\ndialogue_color = "cyan"   # the one I like\n'
        )
        migrated = rename_section("ui", "terminal")(text)
        assert migrated == (
            "[settings]\nshow_banner = true\n"
            '\n[terminal]\n# mine\ndialogue_color = "cyan"   # the one I like\n'
        )

    def test_a_file_already_renamed_is_untouched(self) -> None:
        # Every launch reruns this; the second one must be a no-op.
        text = '[terminal]\ndialogue_color = "auto"\n'
        assert rename_section("ui", "terminal")(text) is text

    def test_a_file_holding_both_is_untouched(self) -> None:
        # Nothing here can merge two sections, and guessing which one
        # the reader meant would lose the other.
        text = '[ui]\ndialogue_color = "cyan"\n\n[terminal]\ndialogue_bold = true\n'
        assert rename_section("ui", "terminal")(text) is text

    def test_no_such_section_is_untouched(self) -> None:
        assert rename_section("ui", "terminal")(BASE) is BASE

    def test_a_mention_in_a_comment_is_not_the_section(self) -> None:
        text = BASE + "# [ui] used to live here\n"
        assert rename_section("ui", "terminal")(text) is text


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

    def test_a_dotted_user_name_is_a_literal_section_first(self) -> None:
        # providers.toml sections carry user-chosen names, dots included —
        # the literal key wins over the dotted walk, so the key still seals.
        text = '["my.server"]\napi_key = "plain"\n'
        migrated = set_key("my.server", "api_key", 'api_key = "sealed:x"')(text)
        assert migrated == '["my.server"]\napi_key = "sealed:x"\n'

    def test_a_quoted_section_name_is_seen(self) -> None:
        # A user may quote a section name; the textual scan must find it,
        # or its api key would silently never seal.
        text = '["my server"]\napi_key = "plain"\n'
        migrated = set_key("my server", "api_key", 'api_key = "sealed:x"')(text)
        assert migrated == '["my server"]\napi_key = "sealed:x"\n'


class TestDropKeyEverywhere:
    def test_removes_the_key_from_every_section(self) -> None:
        text = (
            "[ollama]\n"
            'url = "http://localhost:11434/v1"\n'
            "supports_thinking = true\n"
            "\n"
            "[kobold]\n"
            'url = "http://localhost:5001/v1"\n'
            "# flipped on while testing R1\n"
            "supports_thinking = false\n"
        )
        migrated = drop_key_everywhere("supports_thinking")(text)
        assert migrated == (
            "[ollama]\n"
            'url = "http://localhost:11434/v1"\n'
            "\n"
            "[kobold]\n"
            'url = "http://localhost:5001/v1"\n'
        )

    def test_a_section_without_the_key_is_untouched(self) -> None:
        text = '[a]\nsupports_thinking = true\n\n[b]\nurl = "x"\n'
        migrated = drop_key_everywhere("supports_thinking")(text)
        assert migrated == '[a]\n\n[b]\nurl = "x"\n'

    def test_absent_everywhere_is_untouched(self) -> None:
        assert drop_key_everywhere("supports_thinking")(BASE) is BASE


class TestRedacted:
    """What a backup may keep: every `api_key` and `password` the edit
    CHANGED is replaced with "[REDACTED]", and everything else is the
    pre-edit text."""

    BEFORE = (
        "[openrouter]\n"
        'url = "https://openrouter.ai/api/v1"\n'
        'api_key = "sk-plain"\n'
        "\n"
        "[ollama]\n"
        'api_key = "enc:already"\n'
        "\n"
        "[web]\n"
        'password = "hunter2"         # the note\n'
    )

    def test_a_secret_the_edit_replaced_is_redacted(self) -> None:
        after = self.BEFORE.replace("sk-plain", "enc:sealed").replace('"hunter2"', '"$scrypt$v"')
        kept = tomllib.loads(redacted(self.BEFORE, after))
        assert kept["openrouter"]["api_key"] == "[REDACTED]"
        assert kept["web"]["password"] == "[REDACTED]"
        assert "sk-plain" not in redacted(self.BEFORE, after)
        assert "hunter2" not in redacted(self.BEFORE, after)

    def test_a_secret_the_edit_left_alone_is_kept(self) -> None:
        # Restoring the backup still restores it.
        after = self.BEFORE.replace("sk-plain", "enc:sealed")
        kept = tomllib.loads(redacted(self.BEFORE, after))
        assert kept["ollama"]["api_key"] == "enc:already"
        assert kept["web"]["password"] == "hunter2"

    def test_everything_that_is_not_a_secret_is_the_pre_edit_text(self) -> None:
        after = self.BEFORE.replace("sk-plain", "enc:sealed").replace(
            "https://openrouter.ai/api/v1", "https://elsewhere.example/v1"
        )
        kept = tomllib.loads(redacted(self.BEFORE, after))
        assert kept["openrouter"]["url"] == "https://openrouter.ai/api/v1"

    def test_a_secret_whose_section_went_away_is_redacted(self) -> None:
        # Moved to another file, sealed there: this backup keeps no copy.
        after = self.BEFORE.split("[ollama]")[0].replace("[openrouter]", "[other]")
        assert "sk-plain" not in redacted(self.BEFORE, after)

    def test_a_secret_in_a_nested_table_is_redacted(self) -> None:
        # An old config's provider sections are tables inside [providers].
        before = '[providers.openrouter]\napi_key = "sk-plain"\n'
        assert "sk-plain" not in redacted(before, "[settings]\n")

    def test_nothing_changed_is_nothing_redacted(self) -> None:
        assert redacted(self.BEFORE, self.BEFORE) == self.BEFORE

    def test_text_that_does_not_parse_is_kept_as_it_is(self) -> None:
        broken = self.BEFORE + "[unfinished"
        assert redacted(broken, self.BEFORE) == broken


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
