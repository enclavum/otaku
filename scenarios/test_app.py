"""The launch itself (`otaku.app`): the key ceremony and what it seals,
the daily backup snapshot, and resuming over a story that is gone.

Encryption runs the real path end to end — the "command" KEK provider
with a scripted retrieve_command — so these stories prove the core
principle, not a stub: sealed bytes on disk, a wrong key refused, a
missing keystore refused BEFORE the ceremony could mint over it."""

import base64
import secrets
import sqlite3
import subprocess
import tomllib
from datetime import datetime

import pytest

from otaku import encryption
from otaku import logging as otaku_logging
from otaku.backend import launch as backend_launch
from otaku.backend.paths import Paths
from otaku.encryption import EncryptionError, PlainCipher
from otaku.formatting import toml_string
from otaku.settings import config as config_mod
from otaku.settings import prompts as prompts_mod
from otaku.settings import providers as providers_mod
from otaku.settings.migrations import surgery
from otaku.settings.migrations.prompt_texts import EXTRACT_0_2_2
from otaku.store import DatabaseError, Store, is_encrypted
from otaku.store import migrations as store_migrations
from otaku.store.database import check_value
from otaku.store.migrations import v2 as store_v2
from otaku.store.migrations import v3 as store_v3
from otaku.store.migrations import v4 as store_v4
from otaku.store.schema import SCHEMA_DDL
from otaku.terminal.tty import BOLD, RESET
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, run_otaku, set_config, set_config_provider
from scenarios.support.server import ModelServer


def load_config(paths: Paths):
    """The launch's config step, reached through its public door
    (`request_log` loads, migrates, and unlocks the way the app does);
    returns (Config, the providers.toml sections, resolved plain where a
    key is sealed the file-key way these scenarios seal)."""
    backend_launch.request_log(paths.root)
    cfg = config_mod.load(paths.config_file)
    providers = providers_mod.load(paths.providers_file)
    return cfg, providers


KEY = base64.b64encode(b"k" * 32).decode()
OTHER_KEY = base64.b64encode(b"x" * 32).decode()


class TestEncryption:
    def test_content_is_sealed_on_disk(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        set_encryption(root, KEY)
        app = launch(root, server)
        try:
            app.play("I enter the hall.")
        finally:
            app.close()
        raw = app.paths.database_file.read_bytes()
        assert b"I enter the hall." not in raw
        assert scripted.CHAT_REPLY.encode() not in raw
        assert is_encrypted(app.paths.database_file) is True
        # The request log is sealed the same way — the recorded answer
        # included, which protects exactly what the database protects.
        for logfile in (app.paths.root / "logs").rglob("*"):
            if logfile.is_file():
                raw_log = logfile.read_bytes()
                assert b"I enter the hall." not in raw_log
                assert scripted.CHAT_REPLY.encode() not in raw_log

    def test_provider_none_stores_readable_plain_text(self, app: App) -> None:
        app.play("I enter the hall.")
        app.close()
        raw = app.paths.database_file.read_bytes()
        assert b"I enter the hall." in raw
        assert is_encrypted(app.paths.database_file) is False

    def test_the_right_key_reopens_the_story(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        set_encryption(root, KEY)
        app = launch(root, server)
        app.play("I enter the hall.")
        app.close()
        relaunched = launch(root, server)
        try:
            assert [m.body for m in relaunched.session.messages] == [
                "I enter the hall.",
                scripted.CHAT_REPLY,
            ]
        finally:
            relaunched.close()

    def test_logs_decrypt_with_the_key(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        set_encryption(root, KEY)
        app = launch(root, server)
        app.play("I enter the hall.")
        app.close()
        result = run_otaku(root, "logs", "requests")
        assert result.returncode == 0
        assert "I enter the hall." in result.stdout

    def test_a_wrong_key_is_refused(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        set_encryption(root, KEY)
        launch(root, server).close()
        set_encryption(root, OTHER_KEY)
        with pytest.raises(EncryptionError, match="Could not unlock"):
            launch(root, server)

    def test_a_missing_keystore_is_refused_before_the_ceremony(
        self, server: ModelServer, tmp_path
    ) -> None:
        # Unlocking without the keystore would mint a fresh key OVER the
        # sealed rows, making them permanently unreadable — refused first.
        root = tmp_path / "state"
        set_encryption(root, KEY)
        app = launch(root, server)
        app.play("I enter the hall.")
        app.close()
        app.paths.keys_file.unlink()
        with pytest.raises(EncryptionError, match="is missing"):
            launch(root, server)


class TestBackups:
    def test_a_daily_snapshot_appears_on_reopen(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        app = launch(root, server)
        app.play("I enter the hall.")
        app.close()
        relaunched = launch(root, server)
        relaunched.close()
        assert any(app.paths.backups_dir.iterdir())

    def test_a_backup_that_cannot_be_written_is_said_not_only_logged(
        self, server: ModelServer, tmp_path
    ) -> None:
        # A snapshot that did not happen is the user's business: they are
        # one crash away from needing it. The launch says so before the
        # banner, and the system log keeps the same fact.
        root = tmp_path / "state"
        app = launch(root, server)
        app.play("I enter the hall.")
        app.close()
        # The backups dir replaced by a file: the daily VACUUM INTO cannot land.
        for stale in app.paths.backups_dir.iterdir():
            stale.unlink()
        app.paths.backups_dir.rmdir()
        app.paths.backups_dir.write_text("not a directory")

        relaunched = launch(root, server)
        try:
            assert any("backup failed" in notice for notice in relaunched.session.notices)
        finally:
            relaunched.close()


class TestRequestLog:
    """The request log pairs every request with its answer: what came
    back, how long it took, what it cost — the day's profile, readable
    without running anything again."""

    def test_an_answer_is_filed_under_its_request(self, app: App) -> None:
        app.play("I enter the hall.")
        log = backend_launch.request_log(app.paths.root)
        stamp = datetime.now().astimezone().strftime("%Y%m%d")
        entries = list(log.read(stamp))
        request = next(e for e in entries if e.kind == "request" and e.purpose == "chat")
        assert request.request_id
        answer = next(
            e for e in entries if e.kind == "answer" and e.request_id == request.request_id
        )
        assert answer.status == "ok"
        assert answer.seconds is not None and answer.seconds >= 0
        assert answer.first_token_seconds is not None
        assert (answer.prompt_tokens, answer.completion_tokens) == (7, 5)  # the scripted usage
        assert answer.body is not None
        assert answer.body["text"] == scripted.CHAT_REPLY

    def test_a_failed_stream_records_its_outcome_and_the_partial(self, app: App) -> None:
        app.server.fail_after = 1
        app.play("I enter the hall.")
        log = backend_launch.request_log(app.paths.root)
        stamp = datetime.now().astimezone().strftime("%Y%m%d")
        answer = next(e for e in log.read(stamp) if e.kind == "answer")
        assert answer.status.startswith("failed")
        assert answer.body is not None
        assert answer.body["text"]  # what had arrived rides the record

    def test_the_rendered_day_ends_with_the_purpose_summary(self, app: App) -> None:
        app.play("I enter the hall.")
        log = backend_launch.request_log(app.paths.root)
        stamp = datetime.now().astimezone().strftime("%Y%m%d")
        page = "".join(otaku_logging.render_requests(log, stamp))
        assert "=== summary" in page
        assert "chat" in page.split("=== summary")[1]


class TestDatabaseGuard:
    def test_a_foreign_file_is_refused_with_the_curated_message(
        self, server: ModelServer, tmp_path
    ) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.database_file.write_bytes(b"this is not a database")
        with pytest.raises(DatabaseError, match="move the file aside"):
            launch(tmp_path / "state", server)

    def test_a_crashed_first_run_heals_as_fresh(self, server: ModelServer, tmp_path) -> None:
        """Creation is one transaction: a crash rolls back to a valid,
        table-less file — which the next launch treats as fresh, instead
        of refusing it forever."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        conn = sqlite3.connect(paths.database_file)
        conn.execute("CREATE TABLE husk (id INTEGER PRIMARY KEY)")
        conn.execute("DROP TABLE husk")
        conn.commit()
        conn.close()
        launch(tmp_path / "state", server).close()  # launching IS the assertion


class TestSchemaMigration:
    """The versioned ladder in store/migrations.py: its docstring's case
    table, held here — the store and the files are what running the app
    cannot show."""

    def test_a_v1_database_migrates_and_the_story_survives(self, tmp_path) -> None:
        paths = _v1_database(tmp_path / "state")
        store = _open(paths)
        try:
            assert any("Database migrated (v1 → v4)" in note.show for note in store.notes)
            (message,) = store.stories.get_messages(1)
            # The v1 `framing` column reads back through the renamed one.
            assert (message.body, message.template) == ("I enter.", "TPL")
            # The v1 character reads whole through the widened row: the
            # added `card` column trails, so nothing shifted into it.
            (keeper,) = store.characters.list(1)
            assert (keeper.name, keeper.description, keeper.card) == (
                "Keeper",
                "warden of the gate",
                None,
            )
            # The v3 rebuild copied the derivative rows whole: the scene
            # and the Keeper's journal crossed the table recreation.
            ids = store.stories.get_messages_ids(1)
            (scene,) = store.scenes.get_current(1, ids)
            assert scene.summary == "The entry."
            assert store.journals.get_current(1, ids)[keeper.id].state == "at the gate"
        finally:
            store.close()
        assert _meta_version(paths) == "4"

    def test_a_migration_that_ran_is_reported_at_launch(
        self, server: ModelServer, tmp_path
    ) -> None:
        # The ladder moved the user's database under them — they are told
        # before the banner, not left to find it in the log.
        root = tmp_path / "state"
        _v1_database(root)
        app = launch(root, server)
        try:
            assert any("migrated" in notice for notice in app.session.notices)
        finally:
            app.close()

    def test_a_current_database_opens_silently(self, tmp_path) -> None:
        paths = _v1_database(tmp_path / "state")
        _open(paths).close()
        second = _open(paths)
        try:
            assert not any("migrated" in note.text for note in second.notes)
        finally:
            second.close()

    def test_migrated_equals_fresh_byte_for_byte(self, tmp_path) -> None:
        # THE invariant the mechanism hangs on: steps transform an old
        # database into exactly what schema.py creates from scratch.
        migrated = _v1_database(tmp_path / "old")
        _open(migrated).close()
        fresh = Paths.resolve(tmp_path / "new")
        fresh.ensure_tree()
        _open(fresh).close()
        assert _master(migrated) == _master(fresh)

    def test_the_pre_migration_backup_preserves_version_1(self, tmp_path) -> None:
        paths = _v1_database(tmp_path / "state")
        _open(paths).close()
        backup = paths.backups_dir / "history-schema-v1.db"
        assert backup.exists()
        conn = sqlite3.connect(backup)
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'messages'").fetchone()
        conn.close()
        assert version[0] == "1"
        assert "framing" in ddl[0]  # the old shape, restorable

    def test_the_widened_check_admits_card(self, tmp_path) -> None:
        paths = _v1_database(tmp_path / "state")
        conn = sqlite3.connect(paths.database_file)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_kind(conn, "card")  # v1 refuses — the CHECK is live
        conn.close()
        _open(paths).close()
        conn = sqlite3.connect(paths.database_file)
        _insert_kind(conn, "card")  # v2 admits it
        with pytest.raises(sqlite3.IntegrityError):
            _insert_kind(conn, "nonsense")  # the tripwire survives the widening
        conn.close()

    def test_a_newer_database_is_refused(self, tmp_path) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        _open(paths).close()
        conn = sqlite3.connect(paths.database_file)
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        with pytest.raises(DatabaseError, match="newer otaku"):
            _open(paths)

    def test_a_failed_step_leaves_version_1_unharmed(self, tmp_path, monkeypatch) -> None:
        def boom(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM messages")  # damage that MUST roll back
            raise RuntimeError("boom")

        paths = _v1_database(tmp_path / "state")
        monkeypatch.setitem(store_migrations._STEPS, 2, boom)
        with pytest.raises(DatabaseError, match="unharmed at version 1"):
            _open(paths)
        assert _meta_version(paths) == "1"
        conn = sqlite3.connect(paths.database_file)
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
        conn.close()

    def test_the_frozen_v1_is_what_the_release_shipped(self) -> None:
        """The non-circular check: the step's precondition constant against
        the schema the v0.2.2 tag actually shipped — the one comparison a
        fixture built FROM the constant can never make. Skips where the
        tag is not reachable (a shallow clone, an sdist)."""
        proc = subprocess.run(
            ["git", "show", "v0.2.2:otaku/store/schema.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            pytest.skip("the v0.2.2 tag is not reachable here")
        shipped = proc.stdout
        for table, frozen in (
            ("messages", store_v2._V1_MESSAGES),
            ("characters", store_v2._V1_CHARACTERS),
            ("scenes", store_v3._V2_SCENES),
            ("journals", store_v3._V2_JOURNALS),
            # token_usage shipped unchanged from v1 through v3, so the
            # v4 step's precondition is checkable against the same tag.
            ("token_usage", store_v4._V3_TOKEN_USAGE),
        ):
            start = shipped.index(f"CREATE TABLE {table}")
            end = shipped.index(");", start) + 1
            assert shipped[start:end] == frozen, table

    def test_the_ladder_resumes_from_where_it_stamped(self, tmp_path, monkeypatch) -> None:
        paths = _v1_database(tmp_path / "state")
        monkeypatch.setitem(store_migrations._STEPS, 3, _raise)
        with pytest.raises(DatabaseError, match="unharmed at version 2"):
            _open(paths)
        assert _meta_version(paths) == "2"  # step 2 committed and stamped
        monkeypatch.setitem(store_migrations._STEPS, 3, store_v3.to_3)
        resumed = _open(paths)
        try:
            assert any("Database migrated (v2 → v4)" in note.show for note in resumed.notes)
        finally:
            resumed.close()
        assert _meta_version(paths) == "4"


class TestConfigMigration:
    def test_a_first_run_writes_both_config_files(self, tmp_path) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        # The sealing key as a file — first run now migrates too, and a
        # real autoconfigured key must never reach the OS keychain here.
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        _cfg, providers = load_config(paths)
        assert "[providers." not in paths.config_file.read_text()
        rendered = paths.providers_file.read_text()
        for section in ("[llamacpp]", "[koboldcpp]", "[ollama]", "[omlx]", "[lmstudio]"):
            assert section in rendered
        assert set(providers) == {"llamacpp", "koboldcpp", "ollama", "omlx", "lmstudio"}

    def test_a_catalog_whose_key_the_shell_carries_is_founded_at_launch(
        self, tmp_path, monkeypatch
    ) -> None:
        # Setting the variable is the deliberate act that adds a cloud
        # provider: its section lands with the fixed url and no key —
        # the key is read at request time, never written — complete with
        # the prompt_cache row in the same launch. A later launch that
        # finds a variable founds the section then.
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        _cfg, providers = load_config(paths)
        assert "openrouter" not in providers and "nanogpt" not in providers
        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        _cfg, providers = load_config(paths)
        assert providers["openrouter"].url == "https://openrouter.ai/api/v1"
        assert providers["openrouter"].api_key == ""
        rendered = paths.providers_file.read_text()
        assert "from-env" not in rendered
        assert rendered.index("[openrouter]") < rendered.index("prompt_cache")
        assert "[nanogpt]" not in rendered

    def test_a_locale_encoded_config_from_windows_0_3_0_heals_to_utf8(
        self, server: ModelServer, tmp_path
    ) -> None:
        """0.3.0's writer passed no encoding, so a native-Windows state
        carries the ANSI codepage — cp1252, where "…" is one byte no
        strict UTF-8 reader accepts. The launch must read such a file
        rather than die, and the migration re-encodes it for good, the
        pre-edit file backed up like any other change."""
        app = launch(tmp_path / "state", server)
        app.close()
        config_file = app.paths.config_file
        text = config_file.read_text(encoding="utf-8") + "# …my note\n"
        config_file.write_bytes(text.replace("\n", "\r\n").encode("cp1252"))
        with pytest.raises(UnicodeDecodeError):
            config_file.read_text(encoding="utf-8")  # the legacy byte is really there

        load_config(app.paths)
        healed = config_file.read_bytes()
        assert b"\r" not in healed  # LF on every platform, or Windows re-mangles
        assert "# …my note" in healed.decode("utf-8")  # the user's line, re-encoded
        assert list(app.paths.config_backups_dir.glob("config-*.toml"))
        # And it CONVERGES: the next launch finds nothing left to heal.
        load_config(app.paths)
        assert config_file.read_bytes() == healed

    def test_an_old_config_gains_the_new_section_and_a_backup(
        self, server: ModelServer, tmp_path
    ) -> None:
        """A config from an older build passes through the launch's
        `load_config` and comes out in the current shape — surgically,
        the user's own lines intact — with the pre-migration file waiting
        in configs/backups/."""
        app = launch(tmp_path / "state", server)
        app.close()
        config_file = app.paths.config_file
        old = "\n".join(
            line
            for line in config_file.read_text().splitlines()
            if not line.startswith(("[terminal]", "dialogue_color =", "dialogue_bold ="))
        )
        config_file.write_text(old + "\n# my note\n")

        cfg, _providers = load_config(app.paths)
        migrated = config_file.read_text()
        assert "[terminal]" in migrated
        assert 'dialogue_color = "auto"' in migrated
        # Anchored below [settings], where a fresh config renders it.
        index = migrated.index
        assert index("[settings]") < index("[terminal]") < index("[context]")
        assert "# my note" in migrated  # the user's own line survived
        assert cfg.dialogue_color == "auto"
        backups = list(app.paths.config_backups_dir.iterdir())
        assert len(backups) == 1
        assert "[terminal]" not in backups[0].read_text()

    def test_an_old_context_section_gains_the_max_context_row(
        self, server: ModelServer, tmp_path
    ) -> None:
        """A config.toml from before the context budget comes out of the
        launch with `max_context` spelled in its [context] section — how
        an upgrader learns the setting exists — and a second launch
        changes nothing."""
        app = launch(tmp_path / "state", server)
        app.close()
        config_file = app.paths.config_file
        old = "\n".join(
            line
            for line in config_file.read_text().splitlines()
            if not line.startswith("max_context")
        )
        config_file.write_text(old + "\n")

        cfg, _providers = load_config(app.paths)
        migrated = config_file.read_text()
        assert "max_context = 0" in migrated
        assert "0 = the model's own max context" in migrated  # the comment rides it
        assert migrated.index("[context]") < migrated.index("max_context")
        assert cfg.max_context == 0
        backups = sorted(app.paths.config_backups_dir.iterdir())
        load_config(app.paths)
        assert config_file.read_text() == migrated
        assert sorted(app.paths.config_backups_dir.iterdir()) == backups

    def test_tail_messages_renames_to_min_tail_and_keeps_its_value(
        self, server: ModelServer, tmp_path
    ) -> None:
        """A config.toml still spelling `tail_messages` comes out of the
        launch with the key renamed in place — the user's own value
        carried over, the new comment riding it, the old key gone."""
        app = launch(tmp_path / "state", server)
        app.close()
        config_file = app.paths.config_file
        old = "\n".join(
            "tail_messages = 77" if line.startswith("min_tail_messages") else line
            for line in config_file.read_text().splitlines()
        )
        assert "tail_messages = 77" in old
        config_file.write_text(old + "\n")

        cfg, _providers = load_config(app.paths)
        migrated = config_file.read_text()
        assert "min_tail_messages = 77" in migrated
        assert "tail_messages = 77\n" not in migrated  # the old spelling is gone
        assert "at least this many recent messages" in migrated  # the comment rides it
        assert cfg.min_tail_messages == 77
        load_config(app.paths)  # converged: a second launch changes nothing
        assert config_file.read_text() == migrated

    def test_an_old_openrouter_section_gains_the_prompt_cache_row(
        self, server: ModelServer, tmp_path
    ) -> None:
        """A providers.toml from before prompt caching comes out of the
        launch with the key spelled in its [openrouter] section — once,
        with its comment, and only there: how an upgrader learns the
        setting exists. A value the user already set is never touched,
        and a file with no such section is not edited at all."""
        root = tmp_path / "state"
        set_config_provider(root, server, name="openrouter")
        paths = Paths.resolve(root)
        # The pre-upgrade shape: the section without the key.
        old = "\n".join(
            line
            for line in paths.providers_file.read_text().splitlines()
            if not line.startswith("prompt_cache")
        )
        paths.providers_file.write_text(old + "\n")

        load_config(paths)
        migrated = paths.providers_file.read_text()
        assert 'prompt_cache = "5m"' in migrated
        assert migrated.count("prompt_cache") == 1  # [openrouter] alone
        assert "1h suits slow-paced play" in migrated  # the comment rides it
        # Anchored inside the section, not appended to the file.
        assert migrated.index("[openrouter]") < migrated.index("prompt_cache")

        # Converged: a second launch changes nothing and takes no backup.
        backups = sorted(paths.config_backups_dir.iterdir())
        load_config(paths)
        assert paths.providers_file.read_text() == migrated
        assert sorted(paths.config_backups_dir.iterdir()) == backups

    def test_a_set_prompt_cache_value_survives_the_migration(
        self, server: ModelServer, tmp_path
    ) -> None:
        root = tmp_path / "state"
        set_config_provider(root, server, name="openrouter", prompt_cache="off")
        paths = Paths.resolve(root)
        load_config(paths)
        assert 'prompt_cache = "off"' in paths.providers_file.read_text()

    def test_a_stale_prompt_template_follows_the_built_in(
        self, server: ModelServer, tmp_path
    ) -> None:
        """A prompts.toml still holding a previous release's exact template
        follows the new built-in at the next launch — the user's own lines
        riding along untouched, the pre-migration file waiting in
        configs/backups/. An edited template would not match and never
        moves (the pure cases in tests/settings/migrations)."""
        app = launch(tmp_path / "state", server)
        app.close()
        prompts_file = app.paths.prompts_file
        stub = prompts_file.read_text()
        stale = stub.replace(
            toml_string(prompts_mod.EXTRACT_DEFAULT),
            toml_string(EXTRACT_0_2_2),
        )
        assert stale != stub  # the stub really held the current built-in
        prompts_file.write_text(stale + "# my note\n")

        load_config(app.paths)
        migrated = prompts_file.read_text()
        assert tomllib.loads(migrated)["extract_prompt"] == prompts_mod.EXTRACT_DEFAULT
        assert "# my note" in migrated
        assert any(p.name.startswith("prompts-") for p in app.paths.config_backups_dir.iterdir())

    def test_a_current_config_is_left_untouched(self, server: ModelServer, tmp_path) -> None:
        app = launch(tmp_path / "state", server)
        app.close()
        before = app.paths.config_file.read_text()
        load_config(app.paths)
        assert app.paths.config_file.read_text() == before
        assert not app.paths.config_backups_dir.exists()

    def test_a_missing_providers_file_is_refounded(self, tmp_path) -> None:
        """A crash between the first-run writes — or a hand deletion —
        heals: the file is founded and the known backends ensured."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.config_file.write_text("[settings]\nshow_banner = false\n")
        _cfg, providers = load_config(paths)
        assert paths.providers_file.exists()
        assert set(providers) == {"llamacpp", "koboldcpp", "ollama", "omlx", "lmstudio"}

    def test_backups_are_private(self, tmp_path) -> None:
        """Every backup is born 0600 in a 0700 dir — a pre-seal copy may
        hold a plain api key."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.providers_file.write_text('[a]\nurl = "x"\napi_key = ""\n')
        assert surgery.update_providers(
            paths.providers_file,
            paths.config_backups_dir,
            [surgery.set_key("a", "url", 'url = "y"')],
        )
        stamp = datetime.now().astimezone().strftime("%Y%m%d")
        backup = paths.config_backups_dir / f"providers-{stamp}.toml"
        assert (backup.stat().st_mode & 0o777) == 0o600
        assert (backup.parent.stat().st_mode & 0o777) == 0o700

    def test_an_old_configs_providers_move_into_their_own_file(self, tmp_path) -> None:
        """The cross-file migration: [providers.*] sections leave for
        providers.toml — as they are, headers unprefixed, the retired
        thinking knob and the plain api key cleaned up at their new
        home — and reruns converge on the same state."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.config_file.write_text(
            "# the box in the closet\n"
            "[providers.mine]\n"
            'url = "http://localhost:9/v1"\n'
            'api_key = "plain-secret"\n'
            "supports_thinking = true\n"
            "\n"
            "[settings]\n"
            "show_banner = false\n"
        )
        _cfg, providers = load_config(paths)
        assert providers["mine"].url == "http://localhost:9/v1"
        assert _unsealed(paths, providers["mine"].api_key) == "plain-secret"
        moved = paths.providers_file.read_text()
        assert "# the box in the closet\n[mine]\n" in moved
        assert "supports_thinking" not in moved
        assert "plain-secret" not in moved  # sealed on the way over
        # The backends this old config never knew arrived alongside.
        assert "[llamacpp]" in moved
        assert "[lmstudio]" in moved
        conf = paths.config_file.read_text()
        assert "[providers.mine]" not in conf
        assert "show_banner = false" in conf
        # The pre-move config waits in backups; a second launch moves
        # nothing and loads the same providers.
        assert any(p.name.startswith("config-") for p in paths.config_backups_dir.iterdir())
        _again, providers2 = load_config(paths)
        assert providers2["mine"].api_key == providers["mine"].api_key

    def test_a_half_done_move_heals_on_the_next_launch(self, tmp_path) -> None:
        """A crash between the move's two writes leaves the section in
        both files; the next launch drops the config copy — the new home
        wins — and converges."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.providers_file.write_text('[mine]\nurl = "http://localhost:9/v1"\napi_key = ""\n')
        paths.config_file.write_text(
            '[providers.mine]\nurl = "http://old:9/v1"\n\n[settings]\nshow_banner = false\n'
        )
        _cfg, providers = load_config(paths)
        assert "[providers.mine]" not in paths.config_file.read_text()
        assert providers["mine"].url == "http://localhost:9/v1"  # the new home won

    def test_a_hand_typed_plain_key_seals_at_the_next_launch(self, tmp_path) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.config_file.write_text("[settings]\nshow_banner = false\n")
        paths.providers_file.write_text(
            '[mine]\nurl = "http://localhost:9/v1"\napi_key = "pasted-plain"\n'
        )
        _cfg, providers = load_config(paths)
        assert "pasted-plain" not in paths.providers_file.read_text()
        assert _unsealed(paths, providers["mine"].api_key) == "pasted-plain"


class TestFirstLaunch:
    """The install experience, end to end: a fresh database seeds the
    sample stories, the user lands in the first of them by name — with
    or without a reachable model — and every model-facing door explains
    itself until /model."""

    def test_a_fresh_database_seeds_the_samples_and_lands_first(self, server, tmp_path) -> None:
        # The first launch over a new database imports every shipped
        # story — native imports, zero model calls — and the user is in
        # the middle of the first by name, memory and all; the rest wait
        # in /stories.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server)
        try:
            assert app.server.requests == []  # seeding never calls a model
            story_id = app.session.story_id
            assert story_id is not None
            story = app.store.stories.get(story_id)
            assert story.title == "The River That Forgot Its Name"
            assert len(app.session.messages) == 14
            ids = app.store.stories.get_messages_ids(story_id)
            assert len(app.store.scenes.get_current(story_id, ids)) == 2
            assert [c.name for c in app.store.characters.list(story_id)] == ["Maren", "Tallis"]
            # The samples ship their whole memory: every scene arrives
            # with its own story-so-far, nothing left for a first pass
            # (and no model on a fresh install) to rebuild.
            assert all(s.history for s in app.store.scenes.get_current(story_id, ids))
            # The second sample landed whole: the long play, memory and all.
            others = [s for s in app.store.stories.list() if s.id != story_id]
            assert [s.title for s in others] == ["The Vermilion Tour"]
            tour_ids = app.store.stories.get_messages_ids(others[0].id)
            assert len(tour_ids) == 318
            tour_scenes = app.store.scenes.get_current(others[0].id, tour_ids)
            assert len(tour_scenes) == 15
            assert all(s.history for s in tour_scenes)
        finally:
            app.close()
        # Remembered: a relaunch resumes the landing and does NOT seed again.
        relaunched = launch(tmp_path / "state", server)
        try:
            assert relaunched.session.story_id == story_id
            assert len(relaunched.store.stories.list()) == 2
        finally:
            relaunched.close()

    def test_a_declined_launch_picker_opens_the_session_anyway(self, server, tmp_path) -> None:
        # Esc in the launch picker is not a cancel: the session opens
        # model-less — the same state /model's Esc leaves behind.
        app = launch(tmp_path / "state", server, spec=None)
        try:
            assert app.session.provider == ""
        finally:
            app.close()

    def test_no_models_still_opens_into_the_sample(self, server, tmp_path, capsys) -> None:
        # Nothing reachable: the session opens without a model, the sample
        # is there to explore, and a turn is kept as story but explains
        # why nothing streams.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec="")
        try:
            assert app.session.provider == ""
            story = app.store.stories.get(app.session.story_id)
            assert story.title == "The River That Forgot Its Name"
            capsys.readouterr()
            app.play("Hello? Is someone there?")
            assert "No model selected" in capsys.readouterr().out
            assert app.server.requests == []
            assert len(app.session.messages) == 15  # the turn is story, kept
        finally:
            app.close()

    def test_a_model_picked_later_revives_the_session(self, server, tmp_path, capsys) -> None:
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec="")
        try:
            app.play("/model generic/test-model")
            assert f"Switched to {BOLD}generic/test-model{RESET}." in capsys.readouterr().out
            app.play("I climb toward the voice.")
            assert app.session.messages[-1].body == scripted.CHAT_REPLY
        finally:
            app.close()

    def test_an_existing_database_never_seeds(self, server, tmp_path) -> None:
        root = tmp_path / "state"
        app = launch(root, server)  # seed_sample off: the database exists now
        app.play("I enter the hall.")
        app.close()
        set_config(root, seed_sample=True)
        relaunched = launch(root, server)
        try:
            assert len(relaunched.store.stories.list()) == 1  # only the played story
        finally:
            relaunched.close()

    def test_the_knob_off_seeds_nothing(self, app: App) -> None:
        # The scenario default: a fresh database, seed_sample = false.
        assert app.session.story_id is None
        assert app.store.stories.list() == []


class TestResume:
    def test_a_deleted_remembered_story_starts_fresh(self, app: App) -> None:
        app.play("I enter the hall.")
        app.store.stories.delete(app.session.story_id)
        relaunched = launch(app.paths.root, app.server)
        try:
            assert relaunched.session.story_id is None
        finally:
            relaunched.close()

    def test_a_remembered_model_of_a_deleted_provider_reports_and_skips(self, app: App) -> None:
        """The launch's own promise (validation has ONE home there): a
        remembered spec the files no longer make sense of is reported in
        the notices and skipped — the session opens model-less, never a
        failed launch."""
        state_file = app.paths.state_file
        text = state_file.read_text().replace(f'"{app.session.full_model_name}"', '"ghost/model"')
        state_file.write_text(text)
        session = backend_launch.open_session(app.paths.root)
        try:
            assert session.provider == "" and session.model == ""
            assert any("ghost/model" in notice for notice in session.notices)
        finally:
            session.close()


def set_encryption(root, key: str) -> None:
    set_config(
        root,
        encryption=config_mod.Encryption(provider="command", retrieve_command=f"echo {key}"),
    )


def _v1_database(root) -> Paths:
    """A schema-1 database exactly as version 1 CREATED it, one played
    turn inside: the current DDL with the `messages` block swapped for the
    shipped v1 text (the step's own precondition constant — which
    `test_the_frozen_v1_is_what_the_release_shipped` holds against the
    real tag), run through the same executescript path `database.py`
    uses."""
    paths = Paths.resolve(root)
    paths.ensure_tree()
    v1_ddl = SCHEMA_DDL
    for table, shipped in (
        ("messages", store_v2._V1_MESSAGES),
        ("characters", store_v2._V1_CHARACTERS),
        # Unchanged v1 → v2, so step 3's preconditions ARE the v1 texts.
        ("scenes", store_v3._V2_SCENES),
        ("journals", store_v3._V2_JOURNALS),
        # Unchanged v1 → v3, so step 4's precondition IS the v1 text.
        ("token_usage", store_v4._V3_TOKEN_USAGE),
    ):
        start = v1_ddl.index(f"CREATE TABLE {table}")
        end = v1_ddl.index(");", start) + 1
        v1_ddl = v1_ddl[:start] + shipped + v1_ddl[end:]
    conn = sqlite3.connect(paths.database_file)
    conn.executescript("BEGIN;" + v1_ddl)
    # fmt: off
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?), (?, ?)",
        ("schema_version", "1", "check", check_value(PlainCipher())),
    )
    # fmt: on
    now = datetime.now().astimezone().isoformat()
    # fmt: off
    conn.execute(
        "INSERT INTO stories (id, created_at, updated_at) VALUES (1, ?, ?)", (now, now)
    )
    conn.execute(
        "INSERT INTO messages (id, story_id, role, kind, body, framing, created_at, updated_at)"
        " VALUES (1, 1, 'user', 'dialogue', ?, ?, ?, ?)",
        (b"I enter.", b"TPL", now, now),
    )
    # fmt: on
    conn.execute("UPDATE stories SET head_id = 1 WHERE id = 1")
    # A character at the v1 row shape: the added `card` column must land
    # BEHIND these values, or this row's timestamps would shift into it.
    # fmt: off
    conn.execute(
        "INSERT INTO characters (id, story_id, name, description, created_at, updated_at)"
        " VALUES (1, 1, ?, ?, ?, ?)",
        (b"Keeper", b"warden of the gate", now, now),
    )
    # A scene and its journal: rows the v3 step must carry across its
    # table rebuilds, not only re-admit.
    conn.execute(
        "INSERT INTO scenes (id, story_id, start_message_id, end_message_id, summary,"
        " created_at, updated_at) VALUES (1, 1, 1, 1, ?, ?, ?)",
        (b"The entry.", now, now),
    )
    conn.execute(
        "INSERT INTO journals (id, story_id, scene_id, character_id, entry, state,"
        " created_at, updated_at) VALUES (1, 1, 1, 1, ?, ?, ?, ?)",
        (b"I watched.", b"at the gate", now, now),
    )
    # fmt: on
    conn.commit()
    conn.close()
    return paths


def _open(paths: Paths) -> Store:
    return Store.open(paths.database_file, PlainCipher(), backups_dir=paths.backups_dir, keep=0)


def _unsealed(paths: Paths, value: str) -> str:
    return encryption.unseal(value, key_file=paths.config_key_file, service=paths.keychain_service)


def _meta_version(paths: Paths) -> str:
    conn = sqlite3.connect(paths.database_file)
    value = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    conn.close()
    return str(value)


def _master(paths: Paths) -> list[tuple]:
    conn = sqlite3.connect(paths.database_file)
    rows = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    conn.close()
    return rows


def _insert_kind(conn, kind: str) -> None:
    now = datetime.now().astimezone().isoformat()
    # fmt: off
    conn.execute(
        "INSERT INTO messages (story_id, role, kind, body, created_at, updated_at)"
        " VALUES (1, 'user', ?, ?, ?, ?)",
        (kind, b"x", now, now),
    )
    # fmt: on
    conn.commit()


def _raise(conn) -> None:
    raise RuntimeError("boom")
