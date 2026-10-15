"""Schema step 11: what a version-10 database becomes.

One change: `cards` gains `position`, the reader's own order of the
codex — appended after the last column by step 5's procedure, its
helpers copied here because a step's helpers are frozen with it; the
record format is positional, so the cards there are read its default
until the step numbers them, in the order they were made (their names
are sealed, and a step never holds the cipher).
"""

import re
import sqlite3

# The `cards` DDL exactly as each version shipped it. V10 is the step's
# precondition (what step 10 wrote); V11 is what it writes.
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

# What changes against V10: the `position` column appended.
_V11_CARDS = """CREATE TABLE cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       BLOB NOT NULL,
    aliases    BLOB,                     -- JSON array, sealed
    tagline    BLOB,                     -- one line for the reader; never sent to the model
    fields     BLOB,                     -- the card's fields as TOML, macros unbound
    pictures   TEXT,                     -- its pictures as a JSON list, see Attachment; the first the portrait; NULL when none
    digests    TEXT,                     -- the card files it was made from, as a JSON list; NULL when none
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0 -- the reader's order, 0 first; a card made goes last
)"""


def to_11(conn: sqlite3.Connection) -> None:
    _rewrite(conn, "cards", _V10_CARDS, _V11_CARDS)
    # fmt: off
    conn.execute(
        "UPDATE cards SET position = (SELECT COUNT(*) FROM cards AS earlier WHERE earlier.id < cards.id)"
    )
    # fmt: on


def _rewrite(conn: sqlite3.Connection, table: str, expect: str, write: str) -> None:
    """One table's stored DDL replaced under `writable_schema`, refused
    unless it matches what the step expects — a hand-edited schema is
    never guessed at. Step 5's procedure, plus the schema-cookie bump
    step 7 added, so the connection rereads what it wrote — the update
    above writes the new column."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    if row is None or _normalized(row[0]) != _normalized(expect):
        raise sqlite3.DatabaseError(f"{table} DDL is not what schema version 10 shipped")
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
