"""Schema step 13: what a version-12 database becomes.

One change, two columns named alike for the one thing they hold — a
character card's profile as TOML (`context.cards.CharacterProfile`):
`cards.fields` becomes `cards.profile`, and `characters.card`, a story
character's copy of its card's, `characters.profile`. A rename in place,
each table's stored DDL rewritten by step 5's procedure, its helpers
copied here because a step's helpers are frozen with it: the record
format is positional, so every row reads its value under the new name,
and no index, constraint or trigger names either column.
"""

import re
import sqlite3

# The two tables' DDL exactly as each version shipped them. V12 is the
# step's precondition (what steps 11 and 10 wrote, unchanged through 12);
# V13 is what it writes — the one column renamed, its comment with it.
_V12_CARDS = """CREATE TABLE cards (
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

_V13_CARDS = """CREATE TABLE cards (
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

_V12_CHARACTERS = """CREATE TABLE characters (
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

_V13_CHARACTERS = """CREATE TABLE characters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    name        BLOB NOT NULL,
    aliases     BLOB,                    -- JSON array, sealed
    description BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    profile     BLOB,                    -- a copy of its card's profile as TOML, with the names it binds; NULL for an extracted one
    provider    TEXT,                    -- who made the row
    model       TEXT,
    card_id     INTEGER REFERENCES cards(id) ON DELETE SET NULL,  -- the codex card it is linked to
    UNIQUE (story_id, id)                -- composite-FK target: same-story references only
)"""


def to_13(conn: sqlite3.Connection) -> None:
    _rewrite(conn, "cards", _V12_CARDS, _V13_CARDS)
    _rewrite(conn, "characters", _V12_CHARACTERS, _V13_CHARACTERS)


def _rewrite(conn: sqlite3.Connection, table: str, expect: str, write: str) -> None:
    """One table's stored DDL replaced under `writable_schema`, refused
    unless it matches what the step expects — a hand-edited schema is
    never guessed at. Step 5's procedure, plus the schema-cookie bump
    step 7 added, so the connection rereads what it wrote."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    if row is None or _normalized(row[0]) != _normalized(expect):
        raise sqlite3.DatabaseError(f"{table} DDL is not what schema version 12 shipped")
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
