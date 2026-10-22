"""The database nucleus: connection + cipher, lifecycle, guards, backups.

`Database.open` owns opening: a fresh file gets the schema, its version,
and a sealed *check canary*; an existing one must present a canary the
session cipher opens — which catches a plaintext database opened with
encryption configured, an encrypted one opened without it, AND a wrong
or replaced key, all before any content is touched. A daily
`VACUUM INTO` snapshot lands in the backups dir on the first open of the
day. Administrative facts (a migration ran, a backup was written or
failed) are RETURNED as `Database.notes` for the caller to log — the
store writes no log itself. A note marked `show` is one the user has to
be TOLD, not merely have recorded for them.
"""

import base64
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Self

from cryptography.exceptions import InvalidTag

from otaku.encryption import Cipher, PlainCipher

_DECRYPT_ERRORS = (InvalidTag, ValueError)

_VERSION_KEY = "schema_version"
_CHECK_KEY = "check"
_CANARY = b"otaku"


class DatabaseError(Exception):
    pass


@dataclass(frozen=True)
class Note:
    """One administrative fact from opening the database. `text` is the
    log line, always recorded; `show` is what to TELL the user, empty
    when the log suffices — the two registers differ (the log speaks in
    lowercase detail, the screen in a sentence), so each carries its
    own wording rather than one being derived from the other."""

    text: str
    show: str = ""


class Database:
    def __init__(self, conn: sqlite3.Connection, cipher: Cipher) -> None:
        self.conn = conn
        self.notes: list[Note] = []  # administrative facts for the caller
        self._cipher = cipher

    @staticmethod
    def connect(path: Path) -> sqlite3.Connection:
        """A connection with the session pragmas; refuses a file that is
        not SQLite with a curated DatabaseError."""
        # Used by one thread at a time, not only by the one that opened it:
        # the app's main connection passes from the launch to the app's own
        # thread (`backend.app.thread`), whose queue keeps it to one.
        conn = sqlite3.connect(path, check_same_thread=False)
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
    def open(cls, db_path: Path, cipher: Cipher, *, backups_dir: Path, keep: int) -> Self:
        """Open (or create) the story database with the session cipher."""
        from otaku.store.schema import SCHEMA_DDL, SCHEMA_VERSION

        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = cls.connect(db_path)
        db = cls(conn, cipher)
        # No tables means fresh: a never-written file, an empty one, or a
        # first run that crashed mid-creation and rolled back. Creation is
        # ONE transaction — schema, version, and canary land together, so
        # a crash can never leave a schema'd husk `_guard` refuses forever;
        # it rolls back to table-less, fresh again on the next launch.
        # fmt: off
        fresh = conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0
        # fmt: on
        if fresh:
            # executescript adds no transaction control of its own: BEGIN
            # opens here, the meta INSERT joins, commit() seals it all.
            conn.executescript("BEGIN;" + SCHEMA_DDL)
            # fmt: off
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?), (?, ?)",
                (_VERSION_KEY, SCHEMA_VERSION, _CHECK_KEY, check_value(cipher)),
            )
            # fmt: on
            conn.commit()
        else:
            # Late import: the ladder raises this module's DatabaseError.
            from otaku.store import migrations

            # Schema surgery runs unenforced — a rebuild step's DROP must
            # not fire cascades into the tables that reference it; the
            # steps check their own consistency, and enforcement returns
            # with the reopen below, or explicitly on the no-op path.
            conn.execute("PRAGMA foreign_keys = OFF")
            migrated = migrations.migrate(conn, db_path, backups_dir)
            if migrated is not None:
                # The stored DDL changed under `writable_schema`: a fresh
                # connection reloads the schema before anything prepares a
                # statement against the old text.
                conn.close()
                conn = cls.connect(db_path)
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    conn.close()
                    raise DatabaseError(
                        f"{db_path} failed its integrity check after migrating; restore the "
                        f"pre-migration backup from {backups_dir}"
                    )
                db = cls(conn, cipher)
                # The ladder speaks for itself: it is told and logged.
                db.notes.append(migrated)
            else:
                conn.execute("PRAGMA foreign_keys = ON")
                db.conn = conn
            cls._guard(db.conn, db_path, cipher)
        if keep > 0:
            db._daily_backup(db_path, backups_dir, keep=keep)
        return db

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
        """A sealed column to text. NULL → "" (never set); a value the
        cipher cannot open → a sentinel, so one bad row cannot fail a
        whole listing."""
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
        canary must unseal to its known plaintext under the session
        cipher; how it fails tells the user what is actually wrong."""
        from otaku.store.schema import SCHEMA_VERSION

        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        except sqlite3.Error as e:
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

    def _daily_backup(self, db_path: Path, backups_dir: Path, *, keep: int) -> None:
        """Once-a-day snapshot, then prune to the newest `keep` files
        matching this function's own naming pattern. `VACUUM INTO` yields
        a consistent, compacted copy even mid-WAL. Best-effort: a failure
        joins `notes` and never blocks opening."""
        stem, suffix = db_path.stem, db_path.suffix
        stamp = datetime.now().astimezone().strftime("%Y%m%d")
        dest = backups_dir / f"{stem}-{stamp}{suffix}"
        if dest.exists():
            return
        try:
            backups_dir.mkdir(parents=True, exist_ok=True)
            self.conn.execute("VACUUM INTO ?", (str(dest),))
            pattern = re.compile(rf"^{re.escape(stem)}-\d{{8}}{re.escape(suffix)}$")
            dated = sorted(p for p in backups_dir.iterdir() if pattern.match(p.name))
            pruned = dated[:-keep]
            for old in pruned:
                old.unlink()
            extra = f", {len(pruned)} old pruned" if pruned else ""
            self.notes.append(Note(f"daily database backup written at {dest.name}{extra}"))
        except (sqlite3.Error, OSError) as e:
            # A backup that did not happen is the user's business, not the
            # log's alone: they are one crash away from needing it.
            self.notes.append(
                Note(f"daily backup failed: {e}", show=f"The daily database backup failed: {e}")
            )


def check_value(cipher: Cipher) -> str:
    """The canary as stored in meta: a known plaintext sealed by the
    session cipher, base64 for the TEXT column. Under provider "none"
    this is just base64 of the literal bytes — readable without any
    key."""
    return base64.b64encode(cipher.seal(_CANARY)).decode()


def is_encrypted(path: Path) -> bool | None:
    """Whether the database's content is sealed — None when there is no
    database (or no canary to judge by). A plaintext read, usable before
    any key ceremony."""
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
