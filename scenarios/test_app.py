"""The launch itself (`otaku.app`): the key ceremony and what it seals,
the daily backup snapshot, resuming over a story that is gone, and the
purge that `otaku uninstall` asks for.

Encryption runs the real path end to end — the "command" KEK provider
with a scripted retrieve_command — so these stories prove the core
principle, not a stub: sealed bytes on disk, a wrong key refused, a
missing keystore refused BEFORE the ceremony could mint over it."""

import base64
import itertools
import re
import secrets
import sqlite3
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from otaku import encryption
from otaku import logging as otaku_logging
from otaku.backend import launch as backend_launch
from otaku.backend.api import Api, ModelRef
from otaku.backend.app.paths import Paths
from otaku.backend.app.pictures import services as backend_files
from otaku.backend.story.messages.schemas import RawFile
from otaku.encryption import Cipher, EncryptionError, PlainCipher
from otaku.formatting import toml_string
from otaku.settings import config as config_mod
from otaku.settings import prompts as prompts_mod
from otaku.settings import providers as providers_mod
from otaku.settings.migrations import surgery
from otaku.settings.migrations.prompt_texts import CARD_FRAMING_0_7_0, EXTRACT_0_2_2
from otaku.store import DatabaseError, Store, is_encrypted
from otaku.store import migrations as store_migrations
from otaku.store.database import Database, check_value
from otaku.store.files import FileStore, Owner
from otaku.store.migrations import v2 as store_v2
from otaku.store.migrations import v3 as store_v3
from otaku.store.migrations import v4 as store_v4
from otaku.store.migrations import v5 as store_v5
from otaku.store.migrations import v6 as store_v6
from otaku.store.migrations import v10 as store_v10
from otaku.store.migrations import v11 as store_v11
from otaku.store.migrations import v12 as store_v12
from otaku.store.migrations import v13 as store_v13
from otaku.store.schema import (
    SCHEMA_DDL,
    SCHEMA_VERSION,
    Attachment,
    Message,
    StoryMode,
    StorySettingDB,
)
from otaku.terminal.tty import BOLD, RESET
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, run_otaku, set_config, set_config_provider
from scenarios.support.server import ModelServer

FILES_KEY = b"k" * 32  # the folder scenarios' raw keys; KEY below is the config's base64
FILES_OTHER_KEY = b"o" * 32
CAT = b"\xff\xd8 not a real jpeg, but the store never looks"
THUMB = b"thumb bytes"
CAT_PHOTO = Path(__file__).parent / "fixtures" / "cat.jpg"


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
            assert [m.body for m in relaunched.messages] == [
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
        app.paths.backups_dir.write_text("not a directory", encoding="utf-8")

        relaunched = launch(root, server)
        try:
            assert any(
                "backup failed" in notice
                for notice in relaunched.api.app.get().launch_facts.notices
            )
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
            assert any(f"(v1 → v{SCHEMA_VERSION})" in note.show for note in store.notes)
            (message,) = store.stories.get_messages(1)
            # The v1 `framing` column reads back through the renamed one.
            assert (message.body, message.template) == ("I enter.", "TPL")
            # The v5 `attachments` column trails: a v1 row reads NULL there,
            # which is a turn without pictures.
            assert message.attachments == ()
            # The v1 character reads whole through the widened row: the
            # added `profile` column trails, so nothing shifted into it.
            (keeper,) = store.characters.list(1)
            assert (keeper.name, keeper.description, keeper.profile) == (
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
            # The v6 `settings` column trails the story's row: a v1 story
            # reads none, takes some, and the table v6 created answers.
            assert store.stories.get_settings(1) == ()
            asking = StorySettingDB("allow_questions", enabled=True)
            store.stories.set_setting(1, asking)
            assert store.stories.get_settings(1) == (asking,)
            # The row carries them, as a message carries its attachments.
            assert store.stories.get(1).settings == (asking,)
            assert store.settings.get("shared_reminder") == ""
            # The v7 `mode` column trails the row: a v1 row reads its
            # DEFAULT there, a turn written in chat mode.
            assert message.mode is StoryMode.CHAT
            # The v9 `provider` and `model` columns trail the three
            # derivatives: a v1 row reads NULL there — nothing recorded
            # which model made it.
            (journal,) = store.journals.list(1)
            for row in (scene, keeper, journal):
                assert (row.provider, row.model) == (None, None)
            # The v10 `card_id` trails the cast: a v1 character reads NULL
            # there — linked to no card — and the codex v10 created is empty
            # until the reader fills it.
            assert keeper.card_id is None
            assert store.cards.list() == []
            card = store.cards.add("Keeper")
            store.characters.set_card_id(keeper.id, card)
            assert store.characters.list(1)[0].card_id == card
            # The v12 fork point trails the story's row: a v1 story was
            # forked from nothing, and reads NULL there.
            assert store.stories.get(1).forked_from_message_id is None
        finally:
            store.close()
        # Step 7 wrote nothing into the rows it found: a record shorter
        # than the schema reads the column's DEFAULT, and no build before
        # it had a story mode.
        conn = sqlite3.connect(paths.database_file)
        try:
            read = conn.execute("SELECT id, mode FROM messages ORDER BY id").fetchall()
        finally:
            conn.close()
        assert read == [(1, "chat"), (2, "chat"), (3, "chat")]
        assert _meta_version(paths) == SCHEMA_VERSION

    def test_a_migration_that_ran_is_reported_at_launch(
        self, server: ModelServer, tmp_path
    ) -> None:
        # The ladder moved the user's database under them — they are told
        # before the banner, not left to find it in the log.
        root = tmp_path / "state"
        _v1_database(root)
        app = launch(root, server)
        try:
            assert any("migrated" in notice for notice in app.api.app.get().launch_facts.notices)
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

    def test_the_widened_checks_admit_a_tool_turn(self, tmp_path) -> None:
        paths = _v1_database(tmp_path / "state")
        conn = sqlite3.connect(paths.database_file)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_row(conn, "tool", "tool")  # v1 refuses — the CHECKs are live
        conn.close()
        _open(paths).close()
        conn = sqlite3.connect(paths.database_file)
        _insert_row(conn, "tool", "tool")  # v8 admits the tool's turn, role and kind
        with pytest.raises(sqlite3.IntegrityError):
            _insert_row(conn, "narrator", "dialogue")  # the tripwire survives the widening
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
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 3
        conn.close()

    def test_the_frozen_v1_is_what_the_release_shipped(self) -> None:
        """The non-circular check: the step's precondition constant against
        the schema the v0.2.2 tag actually shipped — the one comparison a
        fixture built FROM the constant can never make. Skips where the
        tag is not reachable (a shallow clone, an sdist)."""
        proc = subprocess.run(
            ["git", "show", "v0.2.2:otaku/store/schema.py"],
            capture_output=True,
            encoding="utf-8",
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
            # stories shipped unchanged from v1 through v5: step 6's too.
            ("stories", store_v6._V5_STORIES),
        ):
            start = shipped.index(f"CREATE TABLE {table}")
            end = shipped.index(");", start) + 1
            assert shipped[start:end] == frozen, table

    def test_the_frozen_v4_is_what_the_release_shipped(self) -> None:
        """The same non-circular check for step 5's precondition, against
        the schema the v0.5.0 tag shipped (version 4)."""
        proc = subprocess.run(
            ["git", "show", "v0.5.0:otaku/store/schema.py"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        if proc.returncode != 0:
            pytest.skip("the v0.5.0 tag is not reachable here")
        shipped = proc.stdout
        start = shipped.index("CREATE TABLE messages")
        end = shipped.index(");", start) + 1
        assert shipped[start:end] == store_v5._V4_MESSAGES

    def test_the_frozen_v9_is_what_the_release_shipped(self) -> None:
        """Step 10's precondition against the schema the v0.7.0 tag shipped
        (version 9): the cast the codex link is appended to."""
        proc = subprocess.run(
            ["git", "show", "v0.7.0:otaku/store/schema.py"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        if proc.returncode != 0:
            pytest.skip("the v0.7.0 tag is not reachable here")
        shipped = proc.stdout
        start = shipped.index("CREATE TABLE characters")
        end = shipped.index(");", start) + 1
        assert shipped[start:end] == store_v10._V9_CHARACTERS

    def test_the_ladder_resumes_from_where_it_stamped(self, tmp_path, monkeypatch) -> None:
        paths = _v1_database(tmp_path / "state")
        monkeypatch.setitem(store_migrations._STEPS, 3, _raise)
        with pytest.raises(DatabaseError, match="unharmed at version 2"):
            _open(paths)
        assert _meta_version(paths) == "2"  # step 2 committed and stamped
        monkeypatch.setitem(store_migrations._STEPS, 3, store_v3.to_3)
        resumed = _open(paths)
        try:
            assert any(f"(v2 → v{SCHEMA_VERSION})" in note.show for note in resumed.notes)
        finally:
            resumed.close()
        assert _meta_version(paths) == SCHEMA_VERSION

    def test_step_11_orders_the_cards_as_they_were_made(self, tmp_path) -> None:
        # A version-10 codex had no order of the reader's: the step numbers
        # its cards in the order they were made — a later move it cannot know.
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            ids = [store.cards.add(name) for name in ("zed", "Alma", "Bo")]
            store.cards.set_order([ids[2], ids[0], ids[1]])
        finally:
            store.close()
        _taken_back(paths, 10)
        store = _open(paths)
        try:
            assert [card.name for card in store.cards.list()] == ["zed", "Alma", "Bo"]
        finally:
            store.close()

    def test_step_12_finds_where_each_fork_was_cut(self, tmp_path, monkeypatch) -> None:
        # No fork before step 12 recorded its cut: the step works it out
        # from the copies — through a branch of the source's own beside
        # the copied chain, a fork's own turns after it, and a fork of a
        # fork — and leaves it unknown where the source is gone.
        _ticking(monkeypatch)
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            source = store.stories.add("A")
            ids = [_say(store, source, f"turn {n}") for n in range(1, 5)]
            store.stories.set_head(source, ids[1], expected_head=ids[-1])
            _say(store, source, "another third turn")
            fork = store.stories.fork(source, from_message_id=ids[2])
            on = _say(store, fork, "the fork goes on")
            again = store.stories.fork(fork)
            gone = store.stories.fork(source, from_message_id=ids[0])
            orphan = store.stories.fork(gone)
            store.stories.delete(gone)
            cuts = {fork: ids[2], again: on, orphan: None}
            # a fork records its own cut from now on
            assert {f: store.stories.get(f).forked_from_message_id for f in (fork, again)} == {
                fork: ids[2],
                again: on,
            }
        finally:
            store.close()
        _taken_back(paths, 11)
        store = _open(paths)
        try:
            assert {f: store.stories.get(f).forked_from_message_id for f in cuts} == cuts
        finally:
            store.close()

    def test_step_13_keeps_each_profile_under_its_new_name(self, tmp_path) -> None:
        # A rename in place: a codex card's fields and a story character's
        # copy read back whole under `profile`.
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            card = store.cards.add("Maren", profile="description = 'A keeper.'")
            story = store.stories.add("A")
            copy = "user = 'Ada'\n\ndescription = 'A keeper.'"
            store.characters.add(story, "Maren", profile=copy, card_id=card)
        finally:
            store.close()
        _taken_back(paths, 12)
        store = _open(paths)
        try:
            assert store.cards.get(card).profile == "description = 'A keeper.'"
            assert [c.profile for c in store.characters.list(story)] == [copy]
        finally:
            store.close()


class TestFilesFolder:
    """The folder beside the database: a turn's pictures on disk, sealed
    with the database's cipher, named by story, day and a short
    digest of the bytes, shared by a fork, swept when no message names them —
    the files are what running the app cannot show."""

    def test_a_picture_lands_sealed_beside_the_database(self, tmp_path) -> None:
        paths = _state(tmp_path)
        store = Store.open(
            paths.database_file, Cipher(FILES_KEY), backups_dir=paths.backups_dir, keep=0
        )
        try:
            name = store.files.add(Owner.STORY, 24, CAT, "image/jpeg", thumb=THUMB)
            # Readable in a listing: the kind, the story, the day, a short
            # digest of the bytes, the picture's own extension — and the
            # thumbnail beside it.
            assert re.fullmatch(r"pic-0024-\d{8}-[0-9a-f]{8}\.jpg", name)
            names = {path.name for path in _files(paths)}
            assert names == {name, name.removesuffix(".jpg") + "-thumb.jpg"}
            sealed = (paths.database_dir / "files" / name).read_bytes()
            assert CAT not in sealed
            assert store.files.get(name) == (CAT, "image/jpeg")
            assert store.files.get_thumb(name) == THUMB
        finally:
            store.close()

    def test_under_provider_none_the_file_is_the_picture(self, tmp_path) -> None:
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            name = store.files.add(Owner.STORY, 1, CAT, "image/jpeg")
            assert (paths.database_dir / "files" / name).read_bytes() == CAT
            assert store.files.get_thumb(name) is None  # none was given
        finally:
            store.close()

    def test_another_key_reads_the_folder_as_absent(self, tmp_path) -> None:
        paths = _state(tmp_path)
        store = Store.open(
            paths.database_file, Cipher(FILES_KEY), backups_dir=paths.backups_dir, keep=0
        )
        try:
            name = store.files.add(Owner.STORY, 1, CAT, "image/jpeg")
        finally:
            store.close()
        other = FileStore(paths.database_dir / "files", Cipher(FILES_OTHER_KEY))
        assert other.get(name) is None

    def test_the_same_picture_twice_in_a_story_is_one_file(self, tmp_path) -> None:
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            first = store.files.add(Owner.STORY, 1, CAT, "image/jpeg")
            assert store.files.add(Owner.STORY, 1, CAT, "image/jpeg") == first
            assert len(_files(paths)) == 1
            # Another story's copy is its own file, under its own number.
            assert store.files.add(Owner.STORY, 2, CAT, "image/jpeg") != first
            assert len(_files(paths)) == 2
        finally:
            store.close()

    def test_a_fork_shares_the_picture(self, tmp_path) -> None:
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            story = store.stories.add("Cats")
            picture = _attach(store, story, CAT)
            store.stories.append(
                story,
                Message(role="user", body="Look.", attachments=(picture,)),
                after=store.stories.get_head(story),
            )
            copy = store.stories.fork(story)
            (message,) = store.stories.get_messages(copy)
            assert message.attachments == (picture,)  # the origin's name, shared
            assert len(_files(paths)) == 1
        finally:
            store.close()

    def test_a_story_deleted_and_swept_takes_what_no_message_names(self, tmp_path) -> None:
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            story = store.stories.add("Cats")
            picture = _attach(store, story, CAT)
            store.stories.append(
                story,
                Message(role="user", body="Look.", attachments=(picture,)),
                after=store.stories.get_head(story),
            )
            copy = store.stories.fork(story)
            store.stories.delete(copy)
            assert store.sweep_files() == 0  # the original still names it
            store.stories.delete(story)
            assert store.sweep_files() == 1
            assert _files(paths) == []
        finally:
            store.close()

    def test_a_card_picture_is_named_by_the_card(self, tmp_path) -> None:
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            name = store.files.add(Owner.CARD, 12, CAT, "image/jpeg")
            assert re.fullmatch(r"crd-0012-\d{8}-[0-9a-f]{8}\.jpg", name)
            # the same bytes for a story are a file of their own
            assert store.files.add(Owner.STORY, 12, CAT, "image/jpeg") != name
        finally:
            store.close()

    def test_the_sweep_keeps_what_a_card_names_and_takes_it_with_the_card(self, tmp_path) -> None:
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            card = store.cards.add("Mira")
            name = store.files.add(Owner.CARD, card, CAT, "image/jpeg", thumb=THUMB)
            store.cards.set_pictures(
                card, [Attachment(file=name, width=2, height=2, size=len(CAT))]
            )
            assert store.sweep_files() == 0
            store.cards.delete(card)
            assert store.sweep_files() == 2  # the picture and its thumbnail
            assert _files(paths) == []
        finally:
            store.close()

    def test_a_card_picture_sent_to_a_story_outlives_the_card(self, tmp_path) -> None:
        # The card row carries the card's file by name, as a fork does a
        # turn's: the story keeps it after the card is gone.
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            card = store.cards.add("Mira")
            name = store.files.add(Owner.CARD, card, CAT, "image/jpeg")
            portrait = Attachment(file=name, width=2, height=2, size=len(CAT))
            store.cards.set_pictures(card, [portrait])
            story = store.stories.add("Cats")
            store.stories.append(
                story,
                Message(role="user", body="/card Mira", kind="card", attachments=(portrait,)),
                after=None,
            )
            store.cards.delete(card)
            assert store.sweep_files() == 0
            assert store.files.get(name) == (CAT, "image/jpeg")
        finally:
            store.close()

    def test_an_abandoned_sibling_keeps_its_picture(self, tmp_path) -> None:
        # Undo moves the head and deletes nothing: the turn stays in the
        # tree, so its picture stays in the folder.
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            story = store.stories.add("Cats")
            picture = _attach(store, story, CAT)
            store.stories.append(
                story,
                Message(role="user", body="Look.", attachments=(picture,)),
                after=store.stories.get_head(story),
            )
            store.stories.set_head(story, None, expected_head=store.stories.get_head(story))
            assert store.sweep_files() == 0
            assert len(_files(paths)) == 1
        finally:
            store.close()

    def test_a_stray_temporary_is_swept(self, tmp_path) -> None:
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            story = store.stories.add("Cats")
            picture = _attach(store, story, CAT)
            store.stories.append(
                story,
                Message(role="user", body="Look.", attachments=(picture,)),
                after=store.stories.get_head(story),
            )
            (paths.database_dir / "files" / ".deadbeef.tmp").write_bytes(b"half")
            assert store.sweep_files() == 1
            assert [path.name for path in _files(paths)] == [picture.file]
        finally:
            store.close()

    def test_the_launch_sweeps_what_a_crash_left(self, server: ModelServer, tmp_path) -> None:
        # Convergent like a settings migration: a file the delete's crash
        # left behind goes on the next launch, and nothing else does.
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            story = store.stories.add("Cats")
            picture = _attach(store, story, CAT)
            store.stories.append(
                story,
                Message(role="user", body="Look.", attachments=(picture,)),
                after=store.stories.get_head(story),
            )
        finally:
            store.close()
        (paths.database_dir / "files" / "pic-0009-20260101-deadbeef.jpg").write_bytes(b"orphan")
        app = launch(paths.root, server)
        try:
            assert [path.name for path in _files(paths)] == [picture.file]
        finally:
            app.close()

    def test_a_photo_read_and_saved_lands_with_its_thumbnail(self, tmp_path) -> None:
        # The real photograph, through the real pipeline, into the folder:
        # the row's facts are the file's, and both files decode.
        paths = _state(tmp_path)
        store = _open(paths)
        try:
            read = backend_files.read_picture(RawFile(CAT_PHOTO.read_bytes(), "cat.jpg"))
            picture = backend_files.save(store.files, read, Owner.STORY, 1)
            assert picture.file.endswith(".jpg")
            assert max(picture.width, picture.height) <= backend_files.WIRE_EDGE
            stored = store.files.get(picture.file)
            assert stored is not None and len(stored[0]) == picture.size
            assert store.files.get_thumb(picture.file) is not None
            names = {path.name for path in _files(paths)}
            assert names == {picture.file, picture.file.removesuffix(".jpg") + "-thumb.jpg"}
        finally:
            store.close()


class TestConfigMigration:
    def test_a_first_run_writes_both_config_files(self, tmp_path) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        # The sealing key as a file — first run now migrates too, and a
        # real autoconfigured key must never reach the OS keychain here.
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        _cfg, providers = load_config(paths)
        assert "[providers." not in paths.config_file.read_text(encoding="utf-8")
        rendered = paths.providers_file.read_text(encoding="utf-8")
        for section in ("[llamacpp]", "[koboldcpp]", "[ollama]", "[omlx]", "[lmstudio]"):
            assert section in rendered
        assert set(providers) == {"llamacpp", "koboldcpp", "ollama", "omlx", "lmstudio"}

    def test_a_first_run_writes_the_integrations_file_too(self, tmp_path) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        load_config(paths)
        # every configurable integration's section with no key: web
        # search's, one key and no engine named
        assert tomllib.loads(paths.integrations_file.read_text(encoding="utf-8")) == {
            "web_search": {"api_key": ""},
        }

    def test_a_missing_integration_section_is_founded_and_a_plain_key_sealed(
        self, tmp_path
    ) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.config_file.write_text("[settings]\nshow_banner = false\n", encoding="utf-8")
        paths.integrations_file.write_text(
            '[proxy]\nurl = "https://x.test/api"\napi_key = "plain-k"\n', encoding="utf-8"
        )
        load_config(paths)
        raw = paths.integrations_file.read_text(encoding="utf-8")
        assert "plain-k" not in raw
        file = tomllib.loads(raw)
        assert file["web_search"] == {"api_key": ""}
        assert _unsealed(paths, file["proxy"]["api_key"]) == "plain-k"

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
        rendered = paths.providers_file.read_text(encoding="utf-8")
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
            for line in config_file.read_text(encoding="utf-8").splitlines()
            if not line.startswith(("[terminal]", "dialogue_color =", "dialogue_bold ="))
        )
        config_file.write_text(old + "\n# my note\n", encoding="utf-8")

        cfg, _providers = load_config(app.paths)
        migrated = config_file.read_text(encoding="utf-8")
        assert "[terminal]" in migrated
        assert 'dialogue_color = "auto"' in migrated
        # Anchored below [settings], where a fresh config renders it.
        index = migrated.index
        assert index("[settings]") < index("[terminal]") < index("[context]")
        assert "# my note" in migrated  # the user's own line survived
        assert cfg.dialogue_color == "auto"
        backups = list(app.paths.config_backups_dir.iterdir())
        assert len(backups) == 1
        assert "[terminal]" not in backups[0].read_text(encoding="utf-8")

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
            for line in config_file.read_text(encoding="utf-8").splitlines()
            if not line.startswith("max_context")
        )
        config_file.write_text(old + "\n", encoding="utf-8")

        cfg, _providers = load_config(app.paths)
        migrated = config_file.read_text(encoding="utf-8")
        assert "max_context = 0" in migrated
        assert "0 = the model's own max context" in migrated  # the comment rides it
        assert migrated.index("[context]") < migrated.index("max_context")
        assert cfg.context_limit == 0
        backups = sorted(app.paths.config_backups_dir.iterdir())
        load_config(app.paths)
        assert config_file.read_text(encoding="utf-8") == migrated
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
            for line in config_file.read_text(encoding="utf-8").splitlines()
        )
        assert "tail_messages = 77" in old
        config_file.write_text(old + "\n", encoding="utf-8")

        cfg, _providers = load_config(app.paths)
        migrated = config_file.read_text(encoding="utf-8")
        assert "min_tail_messages = 77" in migrated
        assert "tail_messages = 77\n" not in migrated  # the old spelling is gone
        assert "at least this many recent messages" in migrated  # the comment rides it
        assert cfg.min_tail_messages == 77
        load_config(app.paths)  # converged: a second launch changes nothing
        assert config_file.read_text(encoding="utf-8") == migrated

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
            for line in paths.providers_file.read_text(encoding="utf-8").splitlines()
            if not line.startswith("prompt_cache")
        )
        paths.providers_file.write_text(old + "\n", encoding="utf-8")

        load_config(paths)
        migrated = paths.providers_file.read_text(encoding="utf-8")
        assert 'prompt_cache = "5m"' in migrated
        assert migrated.count("prompt_cache") == 1  # [openrouter] alone
        assert "1h suits slow-paced play" in migrated  # the comment rides it
        # Anchored inside the section, not appended to the file.
        assert migrated.index("[openrouter]") < migrated.index("prompt_cache")

        # Converged: a second launch changes nothing and takes no backup.
        backups = sorted(paths.config_backups_dir.iterdir())
        load_config(paths)
        assert paths.providers_file.read_text(encoding="utf-8") == migrated
        assert sorted(paths.config_backups_dir.iterdir()) == backups

    def test_a_set_prompt_cache_value_survives_the_migration(
        self, server: ModelServer, tmp_path
    ) -> None:
        root = tmp_path / "state"
        set_config_provider(root, server, name="openrouter", prompt_cache="off")
        paths = Paths.resolve(root)
        load_config(paths)
        assert 'prompt_cache = "off"' in paths.providers_file.read_text(encoding="utf-8")

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
        stub = prompts_file.read_text(encoding="utf-8")
        stale = stub.replace(
            toml_string(prompts_mod.EXTRACT_DEFAULT),
            toml_string(EXTRACT_0_2_2),
        )
        assert stale != stub  # the stub really held the current built-in
        prompts_file.write_text(stale + "# my note\n", encoding="utf-8")

        load_config(app.paths)
        migrated = prompts_file.read_text(encoding="utf-8")
        assert tomllib.loads(migrated)["extract_prompt"] == prompts_mod.EXTRACT_DEFAULT
        assert "# my note" in migrated
        assert any(p.name.startswith("prompts-") for p in app.paths.config_backups_dir.iterdir())

    def test_the_card_template_that_left_its_block_open_follows_the_built_in(
        self, server: ModelServer, tmp_path
    ) -> None:
        app = launch(tmp_path / "state", server)
        app.close()
        prompts_file = app.paths.prompts_file
        stub = prompts_file.read_text(encoding="utf-8")
        stale = stub.replace(
            toml_string(prompts_mod.CARD_FRAMING_DEFAULT), toml_string(CARD_FRAMING_0_7_0)
        )
        assert stale != stub
        prompts_file.write_text(stale, encoding="utf-8")
        load_config(app.paths)
        migrated = tomllib.loads(prompts_file.read_text(encoding="utf-8"))
        assert migrated["card_framing"] == prompts_mod.CARD_FRAMING_DEFAULT

    def test_a_current_config_is_left_untouched(self, server: ModelServer, tmp_path) -> None:
        app = launch(tmp_path / "state", server)
        app.close()
        before = app.paths.config_file.read_text(encoding="utf-8")
        load_config(app.paths)
        assert app.paths.config_file.read_text(encoding="utf-8") == before
        assert not app.paths.config_backups_dir.exists()

    def test_a_missing_providers_file_is_refounded(self, tmp_path) -> None:
        """A crash between the first-run writes — or a hand deletion —
        heals: the file is founded and the known backends ensured."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.config_file.write_text("[settings]\nshow_banner = false\n", encoding="utf-8")
        _cfg, providers = load_config(paths)
        assert paths.providers_file.exists()
        assert set(providers) == {"llamacpp", "koboldcpp", "ollama", "omlx", "lmstudio"}

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX mode bits")
    def test_backups_are_private(self, tmp_path) -> None:
        """Every backup is born 0600 in a 0700 dir — a pre-seal copy may
        hold a plain api key."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.providers_file.write_text('[a]\nurl = "x"\napi_key = ""\n', encoding="utf-8")
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
            "show_banner = false\n",
            encoding="utf-8",
        )
        _cfg, providers = load_config(paths)
        assert providers["mine"].url == "http://localhost:9/v1"
        assert _unsealed(paths, providers["mine"].api_key) == "plain-secret"
        moved = paths.providers_file.read_text(encoding="utf-8")
        assert "# the box in the closet\n[mine]\n" in moved
        assert "supports_thinking" not in moved
        assert "plain-secret" not in moved  # sealed on the way over
        # The backends this old config never knew arrived alongside.
        assert "[llamacpp]" in moved
        assert "[lmstudio]" in moved
        conf = paths.config_file.read_text(encoding="utf-8")
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
        paths.providers_file.write_text(
            '[mine]\nurl = "http://localhost:9/v1"\napi_key = ""\n', encoding="utf-8"
        )
        paths.config_file.write_text(
            '[providers.mine]\nurl = "http://old:9/v1"\n\n[settings]\nshow_banner = false\n',
            encoding="utf-8",
        )
        _cfg, providers = load_config(paths)
        assert "[providers.mine]" not in paths.config_file.read_text(encoding="utf-8")
        assert providers["mine"].url == "http://localhost:9/v1"  # the new home won

    def test_a_hand_typed_plain_key_seals_at_the_next_launch(self, tmp_path) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.config_file.write_text("[settings]\nshow_banner = false\n", encoding="utf-8")
        paths.providers_file.write_text(
            '[mine]\nurl = "http://localhost:9/v1"\napi_key = "pasted-plain"\n', encoding="utf-8"
        )
        _cfg, providers = load_config(paths)
        assert "pasted-plain" not in paths.providers_file.read_text(encoding="utf-8")
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
            story_id = app.story_id
            assert story_id is not None
            story = app.store.stories.get(story_id)
            assert story.title == "The River That Forgot Its Name"
            assert len(app.messages) == 16
            # The sample's pictures, shipped in the samples' images, landed
            # on the message they are named for, through the app's own
            # intake: the files and their thumbnails are in the folder.
            pictured = app.messages[14]
            assert pictured.role == "user" and len(pictured.attachments) == 2
            for picture in pictured.attachments:
                assert app.store.files.get(picture.file) is not None
                assert app.store.files.get_thumb(picture.file) is not None
                assert picture.width <= 1568 and picture.height <= 1568
            assert all(not m.attachments for i, m in enumerate(app.messages) if i != 14)
            ids = app.store.stories.get_messages_ids(story_id)
            assert len(app.store.scenes.get_current(story_id, ids)) == 2
            assert [c.name for c in app.store.characters.list(story_id)] == ["Maren", "Tallis"]
            # The samples ship their whole memory: every scene arrives
            # with its own story-so-far, nothing left for a first pass
            # (and no model on a fresh install) to rebuild.
            assert all(s.history for s in app.store.scenes.get_current(story_id, ids))
            # The river ends on a question, and an off tool's calls are
            # neither drawn nor sent: it lands with the questions tool on.
            assert [s.name for s in story.settings if s.enabled] == ["allow_questions"]
            # The second sample landed whole: the long play, memory and all.
            others = [s for s in app.store.stories.list() if s.id != story_id]
            assert [s.title for s in others] == ["The Vermilion Tour"]
            tour = app.store.stories.get(others[0].id)
            assert not [s.name for s in tour.settings if s.enabled]
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
            assert relaunched.story_id == story_id
            assert len(relaunched.store.stories.list()) == 2
        finally:
            relaunched.close()

    def test_the_samples_characters_are_kept_in_all_characters(self, server, tmp_path) -> None:
        # The samples' manifest names their main characters: each a card
        # made from its story's character and linked to it, the fields its
        # row gives set and its portrait added; the rest stay unlinked, for
        # a first look at linking one.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server)
        try:
            manifest = tomllib.loads(
                (Path(backend_launch.__file__).parent / "samples" / "cards.toml").read_text(
                    encoding="utf-8"
                )
            )
            named = {row["name"]: row["tagline"] for row in manifest["card"]}
            cards = app.api.cards.list()
            assert {card.name: card.tagline for card in cards} == named
            assert all(card.story_count == 1 for card in cards)
            rows = {row["name"]: row for row in manifest["card"]}
            for card in cards:
                whole = app.api.cards.get(card.id)
                (linked,) = whole.stories
                assert linked.name == card.name
                assert whole.description  # the row's, else the story's own
                row = rows[card.name]
                for key in ("description", "personality", "scenario", "greeting"):
                    if key in row:
                        assert getattr(whole, key) == row[key]
                if "examples" in row:
                    assert whole.examples == tuple(row["examples"])
                # its portrait, where the row names one, through the intake
                assert len(whole.pictures) == (1 if "picture" in row else 0)
                for picture in whole.pictures:
                    assert app.store.files.get(picture.file) is not None
            unlinked = app.api.cards.unlinked.list()
            assert unlinked and not {row.name for row in unlinked} & set(named)
            assert app.server.requests == []  # still no model called
        finally:
            app.close()

    def test_a_declined_launch_picker_opens_the_app_anyway(self, server, tmp_path) -> None:
        # Esc in the launch picker is not a cancel: the app opens
        # model-less — the same state /model's Esc leaves behind.
        app = launch(tmp_path / "state", server, spec=None)
        try:
            assert app.api.app.get().model is None
        finally:
            app.close()

    def test_no_models_still_opens_into_the_sample(self, server, tmp_path, capsys) -> None:
        # Nothing reachable: the app opens without a model, the sample
        # is there to explore, and a turn is kept as story but explains
        # why nothing streams.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec="")
        try:
            assert app.api.app.get().model is None
            story = app.store.stories.get(app.story_id)
            assert story.title == "The River That Forgot Its Name"
            capsys.readouterr()
            app.play("Hello? Is someone there?")
            assert "No model selected" in capsys.readouterr().out
            assert app.server.requests == []
            assert len(app.messages) == 17  # the turn is story, kept
        finally:
            app.close()

    def test_a_model_picked_later_revives_the_app(self, server, tmp_path, capsys) -> None:
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec="")
        try:
            app.play("/model generic/test-model")
            assert f"Switched to {BOLD}generic/test-model{RESET}." in capsys.readouterr().out
            app.play("I climb toward the voice.")
            assert app.messages[-1].body == scripted.CHAT_REPLY
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
        assert app.story_id is None
        assert app.store.stories.list() == []


class TestPurge:
    """The state dir deleted — otaku's own folders, and the dir itself
    where they were all it held — with its keys in the OS keychain."""

    def test_a_used_state_dir_is_gone(self, app: App) -> None:
        app.play("I enter the hall.")
        root = app.paths.root
        app.close()
        assert backend_launch.purge(root) == root
        assert not root.exists()

    def test_what_else_the_dir_holds_stays_and_the_dir_with_it(self, app: App) -> None:
        root = app.paths.root
        (root / "notes.txt").write_text("mine", encoding="utf-8")
        app.close()
        backend_launch.purge(root)
        assert sorted(entry.name for entry in root.iterdir()) == ["notes.txt"]

    def test_a_dir_never_made_is_purged_without_a_fault(self, tmp_path: Path) -> None:
        # nothing there, nothing under its keychain service: nothing fails
        backend_launch.purge(tmp_path / "state")


class TestResume:
    def test_a_deleted_remembered_story_starts_fresh(self, app: App) -> None:
        app.play("I enter the hall.")
        app.store.stories.delete(app.story_id)
        relaunched = launch(app.paths.root, app.server)
        try:
            assert relaunched.story_id is None
        finally:
            relaunched.close()

    def test_a_remembered_model_of_a_deleted_provider_reports_and_skips(self, app: App) -> None:
        """The launch's own promise (validation has ONE home there): a
        remembered spec the files no longer make sense of is reported in
        the notices and skipped — the app opens model-less, never a failed
        launch."""
        state_file = app.paths.state_file
        model = app.api.app.get().model
        assert model is not None
        text = state_file.read_text(encoding="utf-8").replace(
            f'"{model.provider}/{model.name}"', '"ghost/model"'
        )
        state_file.write_text(text, encoding="utf-8")
        opened = backend_launch.open_app(app.paths.root)
        try:
            assert Api(opened).app.get().model is None
            assert any(
                "ghost/model" in notice for notice in Api(opened).app.get().launch_facts.notices
            )
        finally:
            opened.close()

    def test_a_saved_extraction_model_of_a_deleted_provider_reports_and_goes(
        self, app: App
    ) -> None:
        # The same promise for the model saved for extraction: reported,
        # dropped, and extraction asks the story's model again.
        app.api.app.update(extraction_model=ModelRef("generic", "extract-model"))
        state_file = app.paths.state_file
        state_file.write_text(
            state_file.read_text(encoding="utf-8").replace(
                '"generic/extract-model"', '"ghost/extract-model"'
            ),
            encoding="utf-8",
        )
        opened = backend_launch.open_app(app.paths.root)
        try:
            extraction = Api(opened).app.get().extraction_model
            assert extraction is not None and not extraction.pinned
            facts = Api(opened).app.get()
            assert facts.model is not None and facts.extraction_model is not None
            followed = (facts.extraction_model.provider, facts.extraction_model.name)
            assert followed == (facts.model.provider, facts.model.name)
            notices = Api(opened).app.get().launch_facts.notices
            assert any("ghost/extract-model" in notice for notice in notices)
        finally:
            opened.close()


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
        # Unchanged v1 → v5, so step 6's precondition IS the v1 text.
        ("stories", store_v6._V5_STORIES),
    ):
        start = v1_ddl.index(f"CREATE TABLE {table}")
        end = v1_ddl.index(");", start) + 1
        v1_ddl = v1_ddl[:start] + shipped + v1_ddl[end:]
    # Version 1 had no `settings` table at all: step 6 creates it — nor
    # the codex's `cards` and the index over the cast's link to it, which
    # step 10 creates.
    for table in ("settings", "cards"):
        start = v1_ddl.index(f"CREATE TABLE {table}")
        v1_ddl = v1_ddl[:start] + v1_ddl[v1_ddl.index(");", start) + 2 :]
    index = v1_ddl.index("CREATE UNIQUE INDEX idx_characters_card")
    v1_ddl = v1_ddl[:index] + v1_ddl[v1_ddl.index(";", index) + 2 :]
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
    # Two replies beside the chain, for step 7's fill: one a provider
    # generated, one a card's greeting (its provider 'card').
    conn.execute(
        "INSERT INTO messages (id, story_id, role, kind, body, provider, created_at, updated_at)"
        " VALUES (2, 1, 'assistant', 'dialogue', ?, 'scenario', ?, ?),"
        " (3, 1, 'assistant', 'dialogue', ?, 'card', ?, ?)",
        (b"The reply.", now, now, b"Greeting.", now, now),
    )
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


def _state(tmp_path) -> Paths:
    paths = Paths.resolve(tmp_path / "state")
    paths.ensure_tree()
    return paths


def _files(paths: Paths) -> list:
    folder = paths.database_dir / "files"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def _attach(store: Store, story_id: int, data: bytes) -> Attachment:
    """A picture into the folder under the story's number, and the row
    that names it."""
    name = store.files.add(Owner.STORY, story_id, data, "image/jpeg")
    return Attachment(file=name, width=2, height=2, size=len(data))


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
    _insert_row(conn, "user", kind)


def _insert_row(conn, role: str, kind: str) -> None:
    now = datetime.now().astimezone().isoformat()
    # fmt: off
    conn.execute(
        "INSERT INTO messages (story_id, role, kind, body, created_at, updated_at)"
        " VALUES (1, ?, ?, ?, ?, ?)",
        (role, kind, b"x", now, now),
    )
    # fmt: on
    conn.commit()


def _say(store: Store, story_id: int, body: str) -> int:
    """A turn played on from the story's head; its id."""
    head = store.stories.get_head(story_id)
    return store.stories.append(story_id, Message(role="user", body=body), after=head)


def _ticking(monkeypatch) -> None:
    """The store's clock a second on at every write, as turns played by
    hand are — step 12 tells a copy from a turn of its own by the second."""
    clock = itertools.count()
    start = datetime(2026, 1, 1).astimezone()

    def now() -> str:
        return (start + timedelta(seconds=next(clock))).isoformat(timespec="seconds")

    monkeypatch.setattr(Database, "now", staticmethod(now))


def _taken_back(paths: Paths, version: int) -> None:
    """A database this build wrote, put back to what `version` shipped:
    each later step's tables as the step found them — the record format is
    positional, so the rows read as that version's — the fork points step
    12 works out emptied, and the stamp set: what the ladder takes up."""
    undo = {
        13: (("cards", store_v13._V12_CARDS), ("characters", store_v13._V12_CHARACTERS)),
        12: (("stories", store_v12._V11_STORIES),),
        11: (("cards", store_v11._V10_CARDS),),
    }
    conn = sqlite3.connect(paths.database_file)
    if version < 12:
        conn.execute("UPDATE stories SET forked_from_message_id = NULL")
    conn.execute("PRAGMA writable_schema = ON")
    for step in range(int(SCHEMA_VERSION), version, -1):
        for table, ddl in undo[step]:
            conn.execute(
                "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?", (ddl, table)
            )
    (cookie,) = conn.execute("PRAGMA schema_version").fetchone()
    conn.execute(f"PRAGMA schema_version = {cookie + 1}")
    conn.execute("PRAGMA writable_schema = OFF")
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(version),))
    conn.commit()
    conn.close()


def _raise(conn) -> None:
    raise RuntimeError("boom")
