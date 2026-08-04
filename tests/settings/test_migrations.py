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
    drop_key,
    ensure_key,
    ensure_section,
    move_key,
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
    def test_inserts_after_the_sections_last_line(self) -> None:
        migrated = ensure_key("settings", "smooth", "smooth = true")(BASE)
        assert migrated == (
            "[settings]\n"
            "show_banner = true            # the session header\n"
            "smooth = true\n"
            "\n"
            "[context]\n"
            "head_messages = 20\n"
        )

    def test_a_present_key_is_untouched(self) -> None:
        assert ensure_key("settings", "show_banner", "show_banner = true")(BASE) is BASE

    def test_a_commented_out_key_counts_as_absent(self) -> None:
        text = "[settings]\n# smooth = false\n\n[context]\nhead_messages = 20\n"
        migrated = ensure_key("settings", "smooth", "smooth = true")(text)
        assert "# smooth = false\nsmooth = true\n" in migrated

    def test_a_deleted_section_means_no_insertion(self) -> None:
        assert ensure_key("display", "smooth", "smooth = true")(BASE) is BASE

    def test_the_last_section_of_the_file_grows_at_its_end(self) -> None:
        migrated = ensure_key("context", "tail_messages", "tail_messages = 150")(BASE)
        assert migrated.endswith("head_messages = 20\ntail_messages = 150\n")

    def test_after_places_the_key_below_its_anchor(self) -> None:
        text = "[settings]\nfirst = 1\nlast = 3\n"
        migrated = ensure_key("settings", "second", "second = 2", after="first")(text)
        assert migrated == "[settings]\nfirst = 1\nsecond = 2\nlast = 3\n"

    def test_a_missing_anchor_key_falls_back_to_the_section_end(self) -> None:
        text = "[settings]\nfirst = 1\nlast = 3\n"
        migrated = ensure_key("settings", "second", "second = 2", after="vanished")(text)
        assert migrated == "[settings]\nfirst = 1\nlast = 3\nsecond = 2\n"


class TestMoveKey:
    def test_moves_the_users_line_verbatim_and_creates_the_section(self) -> None:
        text = (
            "[settings]\n"
            "show_banner = true\n"
            "smooth_streaming = false   # my terminal hates it\n"
            "\n"
            "[context]\n"
            "head_messages = 20\n"
        )
        migrated = move_key("settings", "display", "smooth_streaming")(text)
        assert "smooth_streaming" not in migrated.split("[display]")[0]
        assert migrated.endswith("\n[display]\nsmooth_streaming = false   # my terminal hates it\n")

    def test_an_attached_comment_moves_along(self) -> None:
        text = (
            "[settings]\n"
            "# turned off on purpose\n"
            "smooth_streaming = false\n"
            "\n"
            "[display]\n"
            'theme = "dark"\n'
        )
        migrated = move_key("settings", "display", "smooth_streaming")(text)
        assert migrated == (
            "[settings]\n"
            "\n"
            "[display]\n"
            'theme = "dark"\n'
            "# turned off on purpose\n"
            "smooth_streaming = false\n"
        )

    def test_present_in_both_places_drops_the_old_copy(self) -> None:
        text = "[settings]\nsmooth_streaming = false\n\n[display]\nsmooth_streaming = true\n"
        migrated = move_key("settings", "display", "smooth_streaming")(text)
        assert migrated == "[settings]\n\n[display]\nsmooth_streaming = true\n"

    def test_nothing_to_move_is_untouched(self) -> None:
        assert move_key("settings", "display", "smooth_streaming")(BASE) is BASE

    def test_applying_twice_changes_nothing(self) -> None:
        text = "[settings]\nsmooth_streaming = false\n"
        migration = move_key("settings", "display", "smooth_streaming")
        once = migration(text)
        assert migration(once) is once


class TestDropKey:
    def test_removes_the_line_and_its_attached_comment(self) -> None:
        text = (
            "[settings]\n"
            "show_banner = true\n"
            "# a knob that no longer exists\n"
            "old_knob = 3\n"
            "\n"
            "[context]\n"
            "head_messages = 20\n"
        )
        migrated = drop_key("settings", "old_knob")(text)
        assert migrated == ("[settings]\nshow_banner = true\n\n[context]\nhead_messages = 20\n")

    def test_a_comment_separated_by_a_blank_stays(self) -> None:
        text = "[settings]\n# general notes about this section\n\nold_knob = 3\n"
        migrated = drop_key("settings", "old_knob")(text)
        assert "# general notes about this section" in migrated
        assert "old_knob" not in migrated

    def test_an_absent_key_is_untouched(self) -> None:
        assert drop_key("settings", "old_knob")(BASE) is BASE


class TestApplyMigrations:
    def test_chains_in_order(self) -> None:
        migrations = [
            ensure_section("display", "[display]"),
            ensure_key("display", "smooth", "smooth = true"),
        ]
        migrated = apply_migrations(BASE, migrations)
        assert migrated.endswith("\n[display]\nsmooth = true\n")

    def test_no_change_returns_the_original_object(self) -> None:
        migrations = [ensure_key("settings", "show_banner", "show_banner = true")]
        assert apply_migrations(BASE, migrations) is BASE

    def test_a_result_that_no_longer_parses_is_discarded(self) -> None:
        migrations = [lambda text: text + "\n[broken"]
        assert apply_migrations(BASE, migrations) is BASE
