"""The database nucleus: connection + cipher, lifecycle, guards, backups.

`Database.open` owns opening: a fresh file gets the schema, its version, and
a sealed *check canary*; an existing one must present a canary the session
cipher opens — which catches a plaintext database opened with encryption
configured, an encrypted one opened without it, AND a wrong or replaced key,
all before any content is touched. A daily `VACUUM INTO` snapshot lands in
database/backups/ on the first open of the day.

A `Database` instance is what every ops class builds on: the sqlite
connection and the seal/unseal helpers that move content through the
session cipher.
"""

import base64
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Self

from cryptography.exceptions import InvalidTag

from otaku.crypto import Cipher, PlainCipher
from otaku.paths import Paths
from otaku.store.schema import SCHEMA_DDL, SCHEMA_VERSION

_DECRYPT_ERRORS = (InvalidTag, ValueError)

_VERSION_KEY = "schema_version"
_CHECK_KEY = "check"
_CANARY = b"otaku"


class DatabaseError(Exception):
    pass


class Database:
    def __init__(self, conn: sqlite3.Connection, cipher: Cipher) -> None:
        self.conn = conn
        self._cipher = cipher

    @staticmethod
    def connect(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        # journal_mode=WAL persists in the file header; the rest are
        # per-connection and must be set on every open.
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.DatabaseError as e:
            # The first PRAGMA reads the file header: a file that is not
            # SQLite at all is refused here, curated, never a traceback.
            conn.close()
            raise DatabaseError(
                f"{path} is not a database this app wrote ({e}); move the file aside"
            ) from e
        return conn

    @classmethod
    def open(cls, paths: Paths, cipher: Cipher, *, backups: int) -> Self:
        """Open (or create) the story database with the session cipher."""
        path = paths.database_file
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists() or path.stat().st_size == 0
        conn = cls.connect(path)
        if fresh:
            conn.executescript(SCHEMA_DDL)
            with conn:
                # fmt: off
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?), (?, ?)",
                    (_VERSION_KEY, SCHEMA_VERSION, _CHECK_KEY, check_value(cipher)),
                )
                # fmt: on
        else:
            cls._guard(conn, path, cipher)
        if backups > 0:
            cls._daily_backup(conn, paths, keep=backups)
        return cls(conn, cipher)

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def now() -> str:
        """Timestamps are local time with offset, written by the app."""
        return datetime.now().astimezone().isoformat(timespec="seconds")

    # ---------- sealing ----------

    def seal(self, text: str) -> bytes:
        return self._cipher.seal(text.encode("utf-8"))

    def seal_opt(self, text: str | None) -> bytes | None:
        """NULL-preserving seal: None stays NULL ("never set"), everything
        else is sealed — so a nullable column never holds a sealed empty
        string pretending to be absent."""
        return None if text is None else self.seal(text)

    def unseal(self, sealed: bytes | None) -> str:
        """A sealed column to text. NULL → "" (never set); a value the cipher
        cannot open → a sentinel, so one bad row cannot fail a whole listing."""
        if not sealed:
            return ""
        try:
            return self._cipher.unseal(bytes(sealed)).decode("utf-8", errors="replace")
        except _DECRYPT_ERRORS:
            return "<decryption failed>"

    def unseal_opt(self, sealed: bytes | None) -> str | None:
        """NULL-preserving unseal: NULL comes back as None, so callers can
        tell "never set" from "set to empty"."""
        return None if sealed is None else self.unseal(sealed)

    # ---------- opening internals ----------

    @classmethod
    def _guard(cls, conn: sqlite3.Connection, path: Path, cipher: Cipher) -> None:
        """Refuse a database this session cannot read correctly. The check
        canary must unseal to its known plaintext under the session cipher;
        how it fails tells the user what is actually wrong."""
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        except sqlite3.Error as e:
            # A foreign or corrupt file — the curated message, never a
            # raw traceback.
            conn.close()
            raise DatabaseError(
                f"{path} is not a database this app wrote ({e}); move the file aside"
            ) from e
        version = meta.get(_VERSION_KEY)
        if version != SCHEMA_VERSION:
            conn.close()
            raise DatabaseError(
                f"{path} uses schema version {version!r}, this build needs "
                f"{SCHEMA_VERSION!r}; move the file aside"
            )
        stored = meta.get(_CHECK_KEY)
        if stored is None:
            conn.close()
            raise DatabaseError(f"{path} carries no check value; not a database this app wrote")
        try:
            opened = cipher.unseal(base64.b64decode(stored)) == _CANARY
        except _DECRYPT_ERRORS:
            opened = False
        if opened:
            return
        conn.close()
        if stored == check_value(PlainCipher()):
            raise DatabaseError(
                f"{path} is not encrypted, but [encryption].provider is set — "
                'set provider = "none", or move the database aside to start an encrypted one'
            )
        if isinstance(cipher, PlainCipher):
            raise DatabaseError(
                f'{path} is encrypted, but [encryption].provider is "none" — '
                "set the provider it was created with"
            )
        raise DatabaseError(
            f"{path} cannot be decrypted with the configured key — the keystore or its "
            "KEK does not match this database; restore the ones it was created with"
        )

    @staticmethod
    def _daily_backup(conn: sqlite3.Connection, paths: Paths, *, keep: int) -> None:
        """Once-a-day snapshot into database/backups/, then prune to the
        newest `keep` files matching this function's own naming pattern.
        `VACUUM INTO` yields a consistent, compacted copy even mid-WAL.
        Best-effort: a failure warns on stderr and never blocks opening."""
        stem = paths.database_file.stem
        suffix = paths.database_file.suffix
        stamp = datetime.now().astimezone().strftime("%Y%m%d")
        dest = paths.backups_dir / f"{stem}-{stamp}{suffix}"
        if dest.exists():
            return
        try:
            paths.backups_dir.mkdir(parents=True, exist_ok=True)
            conn.execute("VACUUM INTO ?", (str(dest),))
            pattern = re.compile(rf"^{re.escape(stem)}-\d{{8}}{re.escape(suffix)}$")
            dated = sorted(p for p in paths.backups_dir.iterdir() if pattern.match(p.name))
            for old in dated[:-keep]:
                old.unlink()
        except (sqlite3.Error, OSError) as e:
            print(f"otaku: daily backup failed: {e}", file=sys.stderr)


def check_value(cipher: Cipher) -> str:
    """The canary as stored in meta: a known plaintext sealed by the session
    cipher, base64 for the TEXT column. Under provider "none" this is just
    base64 of the literal bytes — readable without any key."""
    return base64.b64encode(cipher.seal(_CANARY)).decode()


def is_encrypted(path: Path) -> bool | None:
    """Whether the database's content is sealed — None when there is no
    database (or no canary to judge by). A plaintext read, so the CLI can
    refuse an encrypted database with a missing keystore BEFORE the key
    ceremony could mint a fresh key over it."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (_CHECK_KEY,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return str(row[0]) != check_value(PlainCipher())
