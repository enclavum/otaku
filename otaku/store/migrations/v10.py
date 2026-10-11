"""Schema step 10: what a version-9 database becomes.

The codex arrives. `cards` is created — a character as the reader keeps
it across stories — and `characters` gains `card_id`, the card a story's
character is linked to, appended after the last column and before the
table constraint by step 5's procedure (the record format is positional,
so existing rows read NULL there: nothing is linked), its helpers copied
here because a step's helpers are frozen with it. The unique index that
allows one link per card per story is created over the new column.
"""

import re
import sqlite3

# The `characters` DDL exactly as each version shipped it. V9 is the
# step's precondition (what version 9 wrote); V10 is what it writes.
_V9_CHARACTERS = """CREATE TABLE characters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    name        BLOB NOT NULL,
    aliases     BLOB,                    -- JSON array, sealed
    description BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    card        BLOB,                    -- an imported card as TOML
    provider    TEXT,                    -- who made the row
    model       TEXT,
    UNIQUE (story_id, id)                -- composite-FK target: same-story references only
)"""

# What changes against V9: the `card_id` column appended.
_V10_CHARACTERS = """CREATE TABLE characters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    name        BLOB NOT NULL,
    aliases     BLOB,                    -- JSON array, sealed
    description BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    card        BLOB,                    -- an imported card as TOML
    provider    TEXT,                    -- who made the row
    model       TEXT,
    card_id     INTEGER REFERENCES cards(id) ON DELETE SET NULL,  -- the codex card it is linked to
    UNIQUE (story_id, id)                -- composite-FK target: same-story references only
)"""

# New in V10, as schema.py spells them: `sqlite_master` keeps a statement
# as written, and a migrated database must equal a fresh one.
_V10_CARDS = """CREATE TABLE cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       BLOB NOT NULL,
    aliases    BLOB,                     -- JSON array, sealed
    tagline    BLOB,                     -- one line for the reader; never sent to the model
    fields     BLOB,                     -- the card's fields as TOML, macros unbound
    pictures   TEXT,                     -- its pictures as a JSON list, see Attachment; the first the portrait; NULL when none
    digests    TEXT,                     -- the card files it was made from, as a JSON list; NULL when none
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""

_V10_CARD_INDEX = (
    "CREATE UNIQUE INDEX idx_characters_card ON characters (card_id, story_id) "
    "WHERE card_id IS NOT NULL"
)


def to_10(conn: sqlite3.Connection) -> None:
    conn.execute(_V10_CARDS)
    _rewrite(conn, "characters", _V9_CHARACTERS, _V10_CHARACTERS)
    conn.execute(_V10_CARD_INDEX)


def _rewrite(conn: sqlite3.Connection, table: str, expect: str, write: str) -> None:
    """One table's stored DDL replaced under `writable_schema`, refused
    unless it matches what the step expects — a hand-edited schema is
    never guessed at. Step 5's procedure, plus the schema-cookie bump
    step 7 added, so the connection rereads what it wrote — the index
    below is created over the column this writes."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    if row is None or _normalized(row[0]) != _normalized(expect):
        raise sqlite3.DatabaseError(f"{table} DDL is not what schema version 9 shipped")
    conn.execute("PRAGMA writable_schema = ON")
    # fmt: off
    conn.execute(
        "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?", (write, table)
    )
    # fmt: on
    (cookie,) = conn.execute("PRAGMA schema_version").fetchone()
    conn.execute(f"PRAGMA schema_version = {int(cookie) + 1}")
    conn.execute("PRAGMA writable_schema = OFF")


def _normalized(sql: str) -> str:
    """DDL with every whitespace run collapsed — the comparison shape for
    preconditions, so an indentation difference never refuses a database
    the step could migrate."""
    return re.sub(r"\s+", " ", sql).strip()
