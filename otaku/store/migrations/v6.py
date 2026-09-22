"""Schema step 6: what a version-5 database becomes.

Two changes. `stories` gains a `settings` column — the story's settings
as one sealed JSON — appended after the last column and before the table
constraint, for step 5's reasons (the record format is positional, so
existing rows read NULL there) and by step 5's procedure, its helpers
copied here because a step's helpers are frozen with it. And the
`settings` table arrives: the settings stories share, sealed — the
first table a step CREATES, so its literal is the statement itself.
"""

import re
import sqlite3

# The `stories` DDL exactly as each version shipped it. V5 is the step's
# precondition (what version 1 wrote, unchanged through 5); V6 is what it
# writes.
_V5_STORIES = """CREATE TABLE stories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    forked_from_id INTEGER,              -- audit: forked from which story; deliberately NOT a FK
    head_id        INTEGER,              -- current position in the message tree
    title          BLOB,
    system         BLOB,                 -- the story's system prompt
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (id, head_id) REFERENCES messages(story_id, id)
)"""

# What changes against V5: the `settings` column appended.
_V6_STORIES = """CREATE TABLE stories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    forked_from_id INTEGER,              -- audit: forked from which story; deliberately NOT a FK
    head_id        INTEGER,              -- current position in the message tree
    title          BLOB,
    system         BLOB,                 -- the story's system prompt
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    settings       BLOB,                 -- the story's settings as JSON, sealed; NULL when none
    FOREIGN KEY (id, head_id) REFERENCES messages(story_id, id)
)"""

# New in V6, as schema.py spells it: `sqlite_master` keeps the statement
# as written, and a migrated database must equal a fresh one.
_V6_SETTINGS = """CREATE TABLE settings (                  -- settings that stories share; the texts that may need sealing
    key        TEXT PRIMARY KEY,         -- 'shared_reminder'
    value      BLOB NOT NULL,            -- sealed; an emptied value deletes its row
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""


def to_6(conn: sqlite3.Connection) -> None:
    _rewrite(conn, "stories", _V5_STORIES, _V6_STORIES)
    conn.execute(_V6_SETTINGS)


def _rewrite(conn: sqlite3.Connection, table: str, expect: str, write: str) -> None:
    """One table's stored DDL replaced under `writable_schema`, refused
    unless it matches what the step expects — a hand-edited schema is
    never guessed at. Adding a COLUMN this way is sound because the
    record format is positional and rows shorter than the schema read as
    NULL in the missing columns, which is exactly what `ADD COLUMN`
    relies on."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    if row is None or _normalized(row[0]) != _normalized(expect):
        raise sqlite3.DatabaseError(f"{table} DDL is not what schema version 5 shipped")
    conn.execute("PRAGMA writable_schema = ON")
    # fmt: off
    conn.execute(
        "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?", (write, table)
    )
    # fmt: on
    conn.execute("PRAGMA writable_schema = OFF")


def _normalized(sql: str) -> str:
    """DDL with every whitespace run collapsed — the comparison shape for
    preconditions, so an indentation difference never refuses a database
    the step could migrate."""
    return re.sub(r"\s+", " ", sql).strip()
