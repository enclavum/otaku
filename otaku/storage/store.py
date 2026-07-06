"""SQLite-backed conversation history with client-side encryption."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag

from otaku.storage.crypto import Cipher

# Hoisted out of the `except (...)` clause: ruff format strips the parens,
# leaving `except InvalidTag, ValueError:` which reads like deprecated
# Python 2 syntax (it parses fine in 3.x as a tuple).
_DECRYPT_ERRORS = (InvalidTag, ValueError)

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS conversations (
    id                 TEXT PRIMARY KEY,
    model              TEXT NOT NULL,
    title_ciphertext   BLOB,
    title_nonce        BLOB,
    summary_ciphertext BLOB,
    summary_nonce      BLOB,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    conversation_id    TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_index         INTEGER NOT NULL,
    role               TEXT NOT NULL,
    content_ciphertext BLOB NOT NULL,
    content_nonce      BLOB NOT NULL,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (conversation_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations (updated_at DESC);
"""


def _db_path(database_url: str) -> Path:
    """Resolve a `sqlite:///`-style URL (or a bare path) to a filesystem path,
    expanding a leading `~`."""
    if database_url.startswith(("postgres://", "postgresql://")):
        raise ValueError(
            "Postgres is no longer supported — set [database].url to a sqlite "
            "path, e.g. sqlite:///~/.otaku/history.db"
        )
    path = database_url
    for prefix in ("sqlite:///", "sqlite://"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    return Path(path).expanduser()


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class Conversation:
    id: UUID
    model: str
    updated_at: datetime
    num_turns: int = 0
    first_user: str = ""
    summary: str = ""
    title: str = ""  # user-set name via /title (independent of the auto summary)


class Store:
    """Persistence handle. Single sqlite3 connection in WAL mode."""

    # Default database config — owned by the storage layer so config.py can
    # assemble the first-run config without hardcoding the backend.
    DEFAULT_URL = "sqlite:///~/.otaku/history.db"
    DEFAULT_CONFIG = f'[database]\nurl = "{DEFAULT_URL}"'

    read_only: bool = False  # `ReadOnlyStore` flips this; callers check it

    def __init__(self, conn: sqlite3.Connection, cipher: Cipher) -> None:
        self._conn = conn
        self._cipher = cipher

    @classmethod
    def open(cls, database_url: str, cipher: Cipher, *, read_only: bool = False) -> Store:
        path = _db_path(database_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        # journal_mode=WAL persists in the file header (set once); the rest are
        # per-connection and must be set on every open.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_DDL)
        # Migrate DBs created before the title columns existed (ALTER only adds
        # when missing; a fresh DB already has them from SCHEMA_DDL).
        cols = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
        if "title_ciphertext" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN title_ciphertext BLOB")
            conn.execute("ALTER TABLE conversations ADD COLUMN title_nonce BLOB")
        klass = ReadOnlyStore if read_only else cls
        return klass(conn, cipher)

    def close(self) -> None:
        self._conn.close()

    def _decrypt_str(self, ct: bytes | None, nonce: bytes | None) -> str:
        """Decrypt a (ciphertext, nonce) pair to a UTF-8 string. Empty pair
        → "" (cleartext column was never set); decryption failure → a
        sentinel so the picker can still render the row."""
        if not ct or not nonce:
            return ""
        try:
            return self._cipher.unseal(bytes(ct), bytes(nonce)).decode("utf-8", errors="replace")
        except _DECRYPT_ERRORS:
            return "<decryption failed>"

    def create_conversation(self, model: str) -> UUID:
        cid = uuid4()
        with self._conn:
            self._conn.execute(
                "INSERT INTO conversations (id, model) VALUES (?, ?)",
                (str(cid), model),
            )
        return cid

    def snapshot_messages(self, conv_id: UUID, messages: list[Message]) -> None:
        """Replace all messages for a conversation. Used after every turn so
        /undo, /regenerate, /clear just re-snapshot the live message list.

        Deliberately does NOT bump `updated_at`: that column tracks summary
        time (set by `update_summary`), and `needs_summary` compares the newest
        message's `created_at` against it. Bumping it here would break refresh.
        """
        with self._conn:
            self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (str(conv_id),))
            for i, m in enumerate(messages):
                ct, nonce = self._cipher.seal(m.content.encode("utf-8"))
                self._conn.execute(
                    """
                    INSERT INTO messages
                        (conversation_id, turn_index, role, content_ciphertext, content_nonce)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(conv_id), i, m.role, ct, nonce),
                )

    def list_conversations(self, limit: int | None = 50) -> list[Conversation]:
        """Conversations, most-recent first. `limit=None` returns all of them
        (used by the /history picker so nothing is silently unreachable)."""
        sql = """
        SELECT c.id, c.model, c.updated_at,
               COALESCE((SELECT COUNT(*) FROM messages m
                          WHERE m.conversation_id = c.id), 0) AS num_turns,
               (SELECT m.content_ciphertext FROM messages m
                  WHERE m.conversation_id = c.id AND m.role = 'user'
                  ORDER BY m.turn_index ASC LIMIT 1) AS first_ct,
               (SELECT m.content_nonce FROM messages m
                  WHERE m.conversation_id = c.id AND m.role = 'user'
                  ORDER BY m.turn_index ASC LIMIT 1) AS first_nonce,
               c.summary_ciphertext, c.summary_nonce,
               c.title_ciphertext, c.title_nonce
          FROM conversations c
         ORDER BY c.updated_at DESC
        """
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        out: list[Conversation] = []
        for (
            cid,
            model,
            updated_at,
            num_turns,
            first_ct,
            first_nonce,
            sum_ct,
            sum_nonce,
            title_ct,
            title_nonce,
        ) in self._conn.execute(sql, params):
            out.append(
                Conversation(
                    id=UUID(cid),
                    model=model,
                    # CURRENT_TIMESTAMP is naive UTC text; make it tz-aware so
                    # the picker's .astimezone() renders local time correctly.
                    updated_at=datetime.fromisoformat(updated_at).replace(tzinfo=UTC),
                    num_turns=num_turns,
                    first_user=self._decrypt_str(first_ct, first_nonce),
                    summary=self._decrypt_str(sum_ct, sum_nonce),
                    title=self._decrypt_str(title_ct, title_nonce),
                )
            )
        return out

    def conversation_texts(self) -> dict[UUID, str]:
        """Full lowercased message text per conversation, for content search.
        Decrypts every message once — the /history picker builds this lazily on
        the first search keystroke so full-content matching reaches text buried
        mid-conversation, not just the summary and first prompt."""
        parts: dict[UUID, list[str]] = {}
        for cid, ct, nonce in self._conn.execute(
            "SELECT conversation_id, content_ciphertext, content_nonce FROM messages"
        ):
            try:
                plain = self._cipher.unseal(bytes(ct), bytes(nonce))
            except _DECRYPT_ERRORS:
                continue
            parts.setdefault(UUID(cid), []).append(plain.decode("utf-8", errors="replace"))
        return {cid: " ".join(chunks).lower() for cid, chunks in parts.items()}

    def load_conversation(self, conv_id: UUID) -> list[Message]:
        """Return all messages of a conversation in turn order."""
        out: list[Message] = []
        for role, ct, nonce in self._conn.execute(
            """
            SELECT role, content_ciphertext, content_nonce
              FROM messages
             WHERE conversation_id = ?
             ORDER BY turn_index ASC
            """,
            (str(conv_id),),
        ):
            pt = self._cipher.unseal(bytes(ct), bytes(nonce))
            out.append(Message(role=role, content=pt.decode("utf-8")))
        return out

    def delete_conversation(self, conv_id: UUID) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM conversations WHERE id = ?", (str(conv_id),))

    def update_summary(self, conv_id: UUID, summary: str) -> None:
        ct, nonce = self._cipher.seal(summary.encode("utf-8"))
        with self._conn:
            self._conn.execute(
                """
                UPDATE conversations
                   SET summary_ciphertext = ?,
                       summary_nonce = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                """,
                (ct, nonce, str(conv_id)),
            )

    def update_title(self, conv_id: UUID, title: str) -> None:
        """Set the conversation's user title. Deliberately does NOT bump
        `updated_at` — titling is metadata and shouldn't reorder the list or
        trip `needs_summary`."""
        ct, nonce = self._cipher.seal(title.encode("utf-8"))
        with self._conn:
            self._conn.execute(
                "UPDATE conversations SET title_ciphertext = ?, title_nonce = ? WHERE id = ?",
                (ct, nonce, str(conv_id)),
            )

    def needs_summary(self, conv_id: UUID) -> bool:
        """True if the conversation has messages and either has no summary yet
        or has changed since the last one.

        `updated_at` is bumped only by `update_summary`, so a message whose
        `created_at` is newer than `updated_at` means the conversation changed
        since it was last summarized.
        """
        row = self._conn.execute(
            """
            SELECT updated_at,
                   summary_ciphertext IS NULL,
                   (SELECT MAX(created_at) FROM messages WHERE conversation_id = ?)
              FROM conversations WHERE id = ?
            """,
            (str(conv_id), str(conv_id)),
        ).fetchone()
        if row is None:
            return False
        updated_at, no_summary, last_message_at = row
        if last_message_at is None:  # no messages
            return False
        if no_summary:
            return True
        # Both are CURRENT_TIMESTAMP text (UTC, same format) → lexical compare.
        return bool(last_message_at > updated_at)


class ReadOnlyStore(Store):
    """Store variant for `otaku -nr` (no-record): every mutating method is a no-op.

    Reads are inherited unchanged so the /history picker, conversation
    load, and previews still work. `create_conversation` returns a
    synthetic UUID so callers that stash it on `state.conv_id` keep
    working — every other write method ignores the value.
    """

    read_only = True

    def create_conversation(self, model: str) -> UUID:
        return uuid4()

    def snapshot_messages(self, conv_id: UUID, messages: list[Message]) -> None:
        return

    def delete_conversation(self, conv_id: UUID) -> None:
        return

    def update_summary(self, conv_id: UUID, summary: str) -> None:
        return

    def update_title(self, conv_id: UUID, title: str) -> None:
        return

    def needs_summary(self, conv_id: UUID) -> bool:
        return False
