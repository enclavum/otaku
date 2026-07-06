"""Tests for the encrypted SQLite conversation store.

`snapshot_messages` is delete-then-rewrite over encrypted, unrecoverable data,
so the roundtrip, replace, and read-only semantics are covered thoroughly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaku.storage.crypto import Cipher
from otaku.storage.store import (
    Conversation,
    Message,
    ReadOnlyStore,
    Store,
    _db_path,
    _default_url,
)


class TestDbPath:
    def test_triple_slash_relative(self) -> None:
        assert _db_path("sqlite:///foo.db") == Path("foo.db")

    def test_quadruple_slash_absolute(self) -> None:
        assert _db_path("sqlite:////abs/foo.db") == Path("/abs/foo.db")

    def test_double_slash(self) -> None:
        assert _db_path("sqlite://foo.db") == Path("foo.db")

    def test_bare_path(self) -> None:
        assert _db_path("/bare/path.db") == Path("/bare/path.db")

    def test_home_expansion(self) -> None:
        # HOME is redirected by the isolation fixture
        p = _db_path("sqlite:///~/x.db")
        assert "~" not in str(p)
        assert p.name == "x.db"

    def test_postgres_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="Postgres is no longer supported"):
            _db_path("postgres://localhost/db")


class TestDefaultUrl:
    def test_default_dir_renders_tilde(self) -> None:
        assert _default_url(Path.home() / ".otaku") == "sqlite:///~/.otaku/history.db"

    def test_custom_dir_inside_home_renders_tilde(self) -> None:
        assert _default_url(Path.home() / ".xyz") == "sqlite:///~/.xyz/history.db"

    def test_dir_outside_home_stays_absolute(self) -> None:
        assert _default_url(Path("/srv/otaku")) == "sqlite:////srv/otaku/history.db"

    def test_roundtrips_through_db_path(self) -> None:
        d = Path.home() / ".alt"
        assert _db_path(_default_url(d)) == d / "history.db"


class TestCreateAndSnapshot:
    def test_create_returns_uuid_and_persists(self, store: Store) -> None:
        cid = store.create_conversation("ollama/llama3")
        assert store.list_conversations()[0].id == cid

    def test_snapshot_roundtrip(self, store: Store) -> None:
        cid = store.create_conversation("m")
        msgs = [Message("system", "be nice"), Message("user", "hi"), Message("assistant", "hey")]
        store.snapshot_messages(cid, msgs)
        assert store.load_conversation(cid) == msgs

    def test_snapshot_replaces_all_rows(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "a"), Message("assistant", "b")])
        store.snapshot_messages(cid, [Message("user", "c")])
        assert store.load_conversation(cid) == [Message("user", "c")]

    def test_content_is_encrypted_at_rest(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "TOPSECRET")])
        raw = store._conn.execute("SELECT content_ciphertext FROM messages").fetchone()[0]
        assert b"TOPSECRET" not in bytes(raw)

    def test_unicode_roundtrip(self, store: Store) -> None:
        cid = store.create_conversation("m")
        msgs = [Message("user", "Привет 🌍 café")]
        store.snapshot_messages(cid, msgs)
        assert store.load_conversation(cid) == msgs


class TestListConversations:
    def test_first_user_and_summary_decrypted(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(
            cid, [Message("system", "s"), Message("user", "my question"), Message("assistant", "a")]
        )
        store.update_summary(cid, "a helpful summary")
        conv = store.list_conversations()[0]
        assert conv.first_user == "my question"
        assert conv.summary == "a helpful summary"
        assert conv.num_turns == 3
        assert conv.model == "m"

    def test_no_user_message_leaves_first_user_empty(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("system", "only system")])
        assert store.list_conversations()[0].first_user == ""

    def test_ordering_by_updated_at_desc(self, store: Store) -> None:
        older = store.create_conversation("m1")
        newer = store.create_conversation("m2")
        store._conn.execute(
            "UPDATE conversations SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (str(older),),
        )
        store._conn.execute(
            "UPDATE conversations SET updated_at = '2030-01-01 00:00:00' WHERE id = ?",
            (str(newer),),
        )
        store._conn.commit()
        ids = [c.id for c in store.list_conversations()]
        assert ids == [newer, older]

    def test_limit(self, store: Store) -> None:
        for _ in range(5):
            store.create_conversation("m")
        assert len(store.list_conversations(limit=2)) == 2

    def test_updated_at_is_tz_aware(self, store: Store) -> None:
        store.create_conversation("m")
        assert store.list_conversations()[0].updated_at.tzinfo is not None

    def test_decryption_failure_shows_sentinel(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "hi")])
        store._conn.execute(
            "UPDATE messages SET content_ciphertext = X'00' WHERE conversation_id = ?", (str(cid),)
        )
        store._conn.commit()
        assert store.list_conversations()[0].first_user == "<decryption failed>"


class TestConversationSearch:
    def test_list_all_with_none_limit(self, store: Store) -> None:
        for i in range(5):
            cid = store.create_conversation("m")
            store.snapshot_messages(cid, [Message("user", f"q{i}")])
        assert len(store.list_conversations(limit=None)) == 5  # nothing capped
        assert len(store.list_conversations(limit=2)) == 2

    def test_conversation_texts_full_content(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(
            cid, [Message("user", "find the NEEDLE here"), Message("assistant", "in the haystack")]
        )
        texts = store.conversation_texts()
        assert "needle" in texts[cid]  # lowercased
        assert "haystack" in texts[cid]  # matches content beyond the first prompt

    def test_conversation_texts_skips_undecryptable(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "readable")])
        store._conn.execute("UPDATE messages SET content_ciphertext = X'00'")
        store._conn.commit()
        # corrupt rows are skipped rather than crashing the whole scan
        assert store.conversation_texts().get(cid, "") == ""


class TestTitle:
    def test_update_and_read(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "q")])
        store.update_title(cid, "My Chat")
        assert store.list_conversations()[0].title == "My Chat"

    def test_default_empty(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "q")])
        assert store.list_conversations()[0].title == ""

    def test_encrypted_at_rest(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.update_title(cid, "TOPSECRETTITLE")
        raw = store._conn.execute(
            "SELECT title_ciphertext FROM conversations WHERE id = ?", (str(cid),)
        ).fetchone()[0]
        assert b"TOPSECRETTITLE" not in bytes(raw)

    def test_does_not_bump_updated_at(self, store: Store) -> None:
        cid = store.create_conversation("m")
        row = "SELECT updated_at FROM conversations WHERE id = ?"
        before = store._conn.execute(row, (str(cid),)).fetchone()[0]
        store.update_title(cid, "t")
        after = store._conn.execute(row, (str(cid),)).fetchone()[0]
        assert before == after  # titling is metadata; must not reorder the list

    def test_migration_adds_columns_to_old_db(self, tmp_path: Path, cipher: Cipher) -> None:
        import sqlite3

        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, model TEXT NOT NULL,
                summary_ciphertext BLOB, summary_nonce BLOB,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE messages (
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                turn_index INTEGER NOT NULL, role TEXT NOT NULL,
                content_ciphertext BLOB NOT NULL, content_nonce BLOB NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (conversation_id, turn_index));
            """
        )
        conn.commit()
        conn.close()
        s = Store.open(f"sqlite:///{db}", cipher)  # opening should ALTER in the title columns
        try:
            cols = {r[1] for r in s._conn.execute("PRAGMA table_info(conversations)")}
            assert {"title_ciphertext", "title_nonce"} <= cols
            cid = s.create_conversation("m")
            s.snapshot_messages(cid, [Message("user", "hi")])
            s.update_title(cid, "migrated")
            assert s.list_conversations()[0].title == "migrated"
        finally:
            s.close()

    def test_readonly_update_title_is_noop(self, ro_store: Store) -> None:
        ro_store.update_title(ro_store.create_conversation("m"), "x")  # must not raise


class TestDelete:
    def test_delete_removes_conversation_and_messages(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "hi")])
        store.delete_conversation(cid)
        assert store.list_conversations() == []
        # FK cascade removed the message rows too
        assert store._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


class TestNeedsSummary:
    def test_no_messages_is_false(self, store: Store) -> None:
        cid = store.create_conversation("m")
        assert store.needs_summary(cid) is False

    def test_missing_conversation_is_false(self, store: Store) -> None:
        import uuid

        assert store.needs_summary(uuid.uuid4()) is False

    def test_messages_without_summary_is_true(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "hi")])
        assert store.needs_summary(cid) is True

    def test_summary_newer_than_messages_is_false(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "hi")])
        store.update_summary(cid, "s")
        store._conn.execute(
            "UPDATE messages SET created_at = '2000-01-01 00:00:00' WHERE conversation_id = ?",
            (str(cid),),
        )
        store._conn.commit()
        assert store.needs_summary(cid) is False

    def test_message_newer_than_summary_is_true(self, store: Store) -> None:
        cid = store.create_conversation("m")
        store.snapshot_messages(cid, [Message("user", "hi")])
        store.update_summary(cid, "s")
        store._conn.execute(
            "UPDATE messages SET created_at = '2100-01-01 00:00:00' WHERE conversation_id = ?",
            (str(cid),),
        )
        store._conn.commit()
        assert store.needs_summary(cid) is True


class TestReadOnlyStore:
    def test_open_read_only_returns_readonly_instance(self, tmp_path: Path, cipher: Cipher) -> None:
        s = Store.open(f"sqlite:///{tmp_path / 'r.db'}", cipher, read_only=True)
        try:
            assert isinstance(s, ReadOnlyStore)
            assert s.read_only is True
        finally:
            s.close()

    def test_mutators_are_noops(self, ro_store: Store) -> None:
        cid = ro_store.create_conversation("m")  # returns synthetic id
        ro_store.snapshot_messages(cid, [Message("user", "hi")])
        ro_store.update_summary(cid, "s")
        assert ro_store.list_conversations() == []
        assert ro_store.needs_summary(cid) is False

    def test_reads_still_work_on_existing_data(self, tmp_path: Path, cipher: Cipher) -> None:
        url = f"sqlite:///{tmp_path / 'shared.db'}"
        rw = Store.open(url, cipher)
        cid = rw.create_conversation("m")
        rw.snapshot_messages(cid, [Message("user", "persisted")])
        rw.close()

        ro = Store.open(url, cipher, read_only=True)
        try:
            assert ro.load_conversation(cid) == [Message("user", "persisted")]
            assert ro.list_conversations()[0].first_user == "persisted"
        finally:
            ro.close()


class TestOpenIdempotent:
    def test_reopen_same_file(self, tmp_path: Path, cipher: Cipher) -> None:
        url = f"sqlite:///{tmp_path / 'again.db'}"
        s1 = Store.open(url, cipher)
        cid = s1.create_conversation("m")
        s1.close()
        s2 = Store.open(url, cipher)  # schema DDL is CREATE IF NOT EXISTS
        try:
            assert s2.list_conversations()[0].id == cid
        finally:
            s2.close()


def test_dataclasses_are_frozen() -> None:
    import uuid
    from dataclasses import FrozenInstanceError
    from datetime import UTC, datetime

    with pytest.raises(FrozenInstanceError):
        Message("user", "x").content = "y"  # type: ignore[misc]
    c = Conversation(id=uuid.uuid4(), model="m", updated_at=datetime.now(UTC))
    with pytest.raises(FrozenInstanceError):
        c.model = "z"  # type: ignore[misc]
