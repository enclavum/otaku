"""Schema step 7: what a version-6 database becomes.

One change. `messages` gains a `mode` column — the mode a row was
written in, 'story' or 'chat' — appended after the last column and
before the table constraints, for step 5's reasons and by step 5's
procedure, its helpers copied here because a step's helpers are frozen
with it. The record format is positional and a row shorter than the
schema reads the missing column's DEFAULT, so every existing row reads
'chat' with nothing written: no build before this one had a story
mode.
"""

import re
import sqlite3

# The `messages` DDL exactly as each version shipped it. V6 is the step's
# precondition (what version 5 wrote, unchanged through 6); V7 is what it
# writes.
_V6_MESSAGES = """CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    parent_id   INTEGER,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
    kind        TEXT NOT NULL DEFAULT 'dialogue'
                  CHECK (kind IN ('dialogue','narration','ooc','card')),
    speaker_id  INTEGER REFERENCES characters(id) ON DELETE SET NULL,  -- extracted automatically
    speaker     BLOB,                    -- extracted automatically; name-at-the-time snapshot
    body        BLOB NOT NULL,           -- exactly what was typed/generated
    template    BLOB,                    -- the template the turn was played with, filled at wire time
    provider    TEXT,                    -- who generated an assistant turn
    model       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    attachments TEXT,                    -- the turn's pictures as a JSON list, see Attachment; NULL when none
    UNIQUE (story_id, id),               -- composite-FK target: same-story references only
    FOREIGN KEY (story_id, parent_id) REFERENCES messages(story_id, id),
    CHECK (parent_id IS NULL OR parent_id < id)
)"""

# What changes against V6: the `mode` column appended.
_V7_MESSAGES = """CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    parent_id   INTEGER,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
    kind        TEXT NOT NULL DEFAULT 'dialogue' CHECK (kind IN ('dialogue','narration','ooc','card')),
    speaker_id  INTEGER REFERENCES characters(id) ON DELETE SET NULL,  -- extracted automatically
    speaker     BLOB,                    -- extracted automatically; name-at-the-time snapshot
    body        BLOB NOT NULL,           -- exactly what was typed/generated
    template    BLOB,                    -- the template the turn was played with, filled at wire time
    provider    TEXT,                    -- who generated an assistant turn
    model       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    attachments TEXT,                    -- the turn's pictures as a JSON list, see Attachment; NULL when none
    mode        TEXT NOT NULL DEFAULT 'chat' CHECK (mode IN ('story','chat')),  -- the mode it was written in
    UNIQUE (story_id, id),               -- composite-FK target: same-story references only
    FOREIGN KEY (story_id, parent_id) REFERENCES messages(story_id, id),
    CHECK (parent_id IS NULL OR parent_id < id)
)"""


def to_7(conn: sqlite3.Connection) -> None:
    _rewrite(conn, "messages", _V6_MESSAGES, _V7_MESSAGES)


def _rewrite(conn: sqlite3.Connection, table: str, expect: str, write: str) -> None:
    """One table's stored DDL replaced under `writable_schema`, refused
    unless it matches what the step expects — a hand-edited schema is
    never guessed at. Adding a COLUMN this way is sound because the
    record format is positional and rows shorter than the schema read as
    the DEFAULT in the missing columns, which is exactly what `ADD
    COLUMN` relies on. Step 5's procedure, plus the schema-cookie bump
    that makes this connection read the column it just added."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    if row is None or _normalized(row[0]) != _normalized(expect):
        raise sqlite3.DatabaseError(f"{table} DDL is not what schema version 6 shipped")
    conn.execute("PRAGMA writable_schema = ON")
    # fmt: off
    conn.execute(
        "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?", (write, table)
    )
    # fmt: on
    # A schema written into sqlite_master by hand is not reread by this
    # connection until the schema cookie moves: bumped here, so what runs
    # after the step on this connection sees the column it just added.
    (cookie,) = conn.execute("PRAGMA schema_version").fetchone()
    conn.execute(f"PRAGMA schema_version = {int(cookie) + 1}")
    conn.execute("PRAGMA writable_schema = OFF")


def _normalized(sql: str) -> str:
    """DDL with every whitespace run collapsed — the comparison shape for
    preconditions, so an indentation difference never refuses a database
    the step could migrate."""
    return re.sub(r"\s+", " ", sql).strip()
