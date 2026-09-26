"""The refreshed-template move: a superseded shipped default follows the
built-in; anything the user touched stays theirs. Pure text in, text
out — the promise is convergence, so the cases parse the result back and
compare values, never the spelling."""

import tomllib

from otaku.formatting import toml_string
from otaku.settings.migrations.prompt_texts import (
    EXTRACT_0_2_2,
    EXTRACT_0_3_0,
    HISTORY_0_3_0,
    STORY_SO_FAR_0_3_0,
    refresh_template,
    rename_template,
)
from otaku.settings.migrations.surgery import apply_migrations
from otaku.settings.prompts import (
    EXTRACT_DEFAULT,
    JOURNAL_HISTORY_DEFAULT,
    SCENE_HISTORY_DEFAULT,
)


class TestRefreshTemplate:
    def test_a_stale_shipped_default_follows_the_built_in(self) -> None:
        text = stub("extract_prompt", EXTRACT_0_2_2)
        migrated = apply_migrations(text, [refresh()])
        assert tomllib.loads(migrated)["extract_prompt"] == EXTRACT_DEFAULT

    def test_an_edited_template_is_never_touched(self) -> None:
        edited = EXTRACT_0_2_2.replace("story analyst", "harsh critic")
        text = stub("extract_prompt", edited)
        assert apply_migrations(text, [refresh()]) == text

    def test_other_keys_ride_along_untouched(self) -> None:
        text = stub("me_framing", "((OOC: I am {name}.))") + stub("extract_prompt", EXTRACT_0_2_2)
        migrated = apply_migrations(text, [refresh()])
        parsed = tomllib.loads(migrated)
        assert parsed["me_framing"] == "((OOC: I am {name}.))"
        assert parsed["extract_prompt"] == EXTRACT_DEFAULT

    def test_a_file_without_the_key_is_untouched(self) -> None:
        text = stub("me_framing", "((OOC: I am {name}.))")
        assert apply_migrations(text, [refresh()]) == text

    def test_the_move_is_idempotent(self) -> None:
        once = apply_migrations(stub("extract_prompt", EXTRACT_0_2_2), [refresh()])
        assert apply_migrations(once, [refresh()]) == once


class TestRenameTemplate:
    def test_the_key_is_renamed_and_its_value_rides_along(self) -> None:
        text = stub("history_prompt", "my own words, with {name} and {entries}")
        parsed = tomllib.loads(apply_migrations(text, [rename()]))
        assert "history_prompt" not in parsed
        assert parsed["journal_history_prompt"] == "my own words, with {name} and {entries}"

    def test_a_body_line_spelling_the_key_is_not_renamed(self) -> None:
        body = "history_prompt = a decoy line\nreal prose with {name} and {entries}"
        migrated = apply_migrations(stub("history_prompt", body), [rename()])
        assert tomllib.loads(migrated)["journal_history_prompt"] == body

    def test_a_file_without_the_key_is_untouched(self) -> None:
        text = stub("me_framing", "((OOC: I am {name}.))")
        assert apply_migrations(text, [rename()]) == text

    def test_the_move_is_idempotent(self) -> None:
        once = apply_migrations(stub("history_prompt", HISTORY_0_3_0), [rename()])
        assert apply_migrations(once, [rename()]) == once

    def test_rename_then_refresh_carries_a_stale_default_to_the_built_in(self) -> None:
        text = stub("history_prompt", HISTORY_0_3_0)
        migrated = apply_migrations(text, [rename(), refresh_history()])
        assert tomllib.loads(migrated)["journal_history_prompt"] == JOURNAL_HISTORY_DEFAULT

    def test_rename_then_refresh_leaves_an_edited_value_theirs(self) -> None:
        edited = HISTORY_0_3_0.replace("300 words", "800 words")
        migrated = apply_migrations(stub("history_prompt", edited), [rename(), refresh_history()])
        assert tomllib.loads(migrated)["journal_history_prompt"] == edited


class TestLanguageRuleRefreshes:
    def test_a_stale_extract_default_follows_the_built_in(self) -> None:
        text = stub("extract_prompt", EXTRACT_0_3_0)
        migrated = apply_migrations(text, [refresh_extract_language()])
        assert tomllib.loads(migrated)["extract_prompt"] == EXTRACT_DEFAULT

    def test_a_scene_rollup_under_its_old_key_converges(self) -> None:
        text = stub("story_so_far_prompt", STORY_SO_FAR_0_3_0)
        moves = [rename_scene(), refresh_scene_language()]
        migrated = apply_migrations(text, moves)
        assert tomllib.loads(migrated)["scene_history_prompt"] == SCENE_HISTORY_DEFAULT

    def test_an_edited_scene_rollup_rides_the_rename_untouched(self) -> None:
        edited = STORY_SO_FAR_0_3_0.replace("4-8 sentences", "a dozen sentences")
        moves = [rename_scene(), refresh_scene_language()]
        migrated = apply_migrations(stub("story_so_far_prompt", edited), moves)
        assert tomllib.loads(migrated)["scene_history_prompt"] == edited


def refresh():
    return refresh_template("extract_prompt", EXTRACT_0_2_2, EXTRACT_DEFAULT)


def rename():
    return rename_template("history_prompt", "journal_history_prompt")


def refresh_history():
    return refresh_template("journal_history_prompt", HISTORY_0_3_0, JOURNAL_HISTORY_DEFAULT)


def refresh_extract_language():
    return refresh_template("extract_prompt", EXTRACT_0_3_0, EXTRACT_DEFAULT)


def rename_scene():
    return rename_template("story_so_far_prompt", "scene_history_prompt")


def refresh_scene_language():
    return refresh_template("scene_history_prompt", STORY_SO_FAR_0_3_0, SCENE_HISTORY_DEFAULT)


def stub(key: str, value: str) -> str:
    """A prompts.toml block the way `write_stub` writes one."""
    return f"{key} = {toml_string(value)}\n\n"
