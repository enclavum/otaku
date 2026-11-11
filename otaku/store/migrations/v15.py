"""Schema step 15: what a version-14 database becomes.

One change, on `cards`: `original_file` appended, the card file a card
was imported from kept as it came — its name in the files folder — and the
pictures' comment saying what they now carry. The column appended after
the last, the stored DDL rewritten by step 5's procedure, its helpers
copied here because a step's helpers are frozen with it. Existing rows
read NULL there: no card made before keeps its file.

The pictures' JSON takes no step: a picture recorded without a kind
reads as a portrait (`store.pictures.CardPicture`), which every one was.
"""

import re
import sqlite3

# The `cards` DDL exactly as each version shipped it. V14 is the step's
# precondition (what version 14 wrote, unchanged since version 13); V15
# is what it writes.
_V14_CARDS = """CREATE TABLE cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       BLOB NOT NULL,
    aliases    BLOB,                     -- JSON array, sealed
    tagline    BLOB,                     -- one line for the reader; never sent to the model
    profile    BLOB,                     -- the card's profile as TOML, the name left to `name`; macros unbound
    pictures   TEXT,                     -- its pictures as a JSON list, see Attachment; the first the portrait; NULL when none
    digests    TEXT,                     -- the card files it was made from, as a JSON list; NULL when none
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0 -- the reader's order, 0 first; a card made goes last
)"""

# What changes against V14: the pictures' comment, and `original_file`.
_V15_CARDS = """CREATE TABLE cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       BLOB NOT NULL,
    aliases    BLOB,                     -- JSON array, sealed
    tagline    BLOB,                     -- one line for the reader; never sent to the model
    profile    BLOB,                     -- the card's profile as TOML, the name left to `name`; macros unbound
    pictures   TEXT,                     -- its pictures as a JSON list, see CardPicture; its portraits first, the first the main one; NULL when none
    digests    TEXT,                     -- the card files it was made from, as a JSON list; NULL when none
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0, -- the reader's order, 0 first; a card made goes last
    original_file TEXT                   -- the card file it was imported from, kept as it came: its name in the files folder; NULL for none
)"""


def to_15(conn: sqlite3.Connection) -> None:
    _rewrite(conn, "cards", _V14_CARDS, _V15_CARDS)


def _rewrite(conn: sqlite3.Connection, table: str, expect: str, write: str) -> None:
    """One table's stored DDL replaced under `writable_schema`, refused
    unless it matches what the step expects — a hand-edited schema is
    never guessed at. Step 5's procedure, plus the schema-cookie bump
    step 7 added, so the connection rereads what it wrote."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    if row is None or _normalized(row[0]) != _normalized(expect):
        raise sqlite3.DatabaseError(f"{table} DDL is not what schema version 14 shipped")
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
