"""Schema step 7: what a version-6 database becomes.

One change. `messages` gains a `completion` column — the completion
that made an assistant turn, 'chat' or 'text' — appended after the
last column and before the table constraints, for step 5's reasons
(the record format is positional, so existing rows read NULL there)
and by step 5's procedure, its helpers copied here because a step's
helpers are frozen with it. Then the rows this build's predecessors
made are filled in: every reply a provider generated came over the
chat wire, the only one there was, so those rows read 'chat'; the
reader's rows, a card's greeting and imported rows, which no completion
made, keep NULL.
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

# What changes against V6: the `completion` column appended.
_V7_MESSAGES = """CREATE TABLE messages (
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
    completion  TEXT,                    -- 'chat' or 'text': the completion that made an assistant turn; NULL on the reader's rows and on rows no completion made
    UNIQUE (story_id, id),               -- composite-FK target: same-story references only
    FOREIGN KEY (story_id, parent_id) REFERENCES messages(story_id, id),
    CHECK (parent_id IS NULL OR parent_id < id)
)"""


def to_7(conn: sqlite3.Connection) -> None:
    _rewrite(conn, "messages", _V6_MESSAGES, _V7_MESSAGES)
    # fmt: off
    conn.execute(
        "UPDATE messages SET completion = 'chat' WHERE role = 'assistant' AND provider IS NOT NULL AND provider != 'card'"
    )
    # fmt: on


def _rewrite(conn: sqlite3.Connection, table: str, expect: str, write: str) -> None:
    """One table's stored DDL replaced under `writable_schema`, refused
    unless it matches what the step expects — a hand-edited schema is
    never guessed at. Adding a COLUMN this way is sound because the
    record format is positional and rows shorter than the schema read as
    NULL in the missing columns, which is exactly what `ADD COLUMN`
    relies on. Step 5's procedure, plus the schema-cookie bump this step
    needs to read the column back within the same connection."""
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
    # connection until the schema cookie moves: bumped here, so the step's
    # own fill below sees the column it just added.
    (cookie,) = conn.execute("PRAGMA schema_version").fetchone()
    conn.execute(f"PRAGMA schema_version = {int(cookie) + 1}")
    conn.execute("PRAGMA writable_schema = OFF")


def _normalized(sql: str) -> str:
    """DDL with every whitespace run collapsed — the comparison shape for
    preconditions, so an indentation difference never refuses a database
    the step could migrate."""
    return re.sub(r"\s+", " ", sql).strip()
