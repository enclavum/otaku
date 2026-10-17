"""Schema step 12: what a version-11 database becomes.

One change: `stories` gains `forked_from_message_id`, the last message a
fork copied from the story it was forked from — appended after the last
column and before the table constraint, by step 5's procedure, its
helpers copied here because a step's helpers are frozen with it; the
names realigned, and `forked_from_id` no longer called audit, since the
two now say what two stories share.

A fork never recorded its cut, so the step works it out once for every
fork whose source still stands. A fork writes its copies first, root
down, and no message is ever deleted, so its messages in id order open
with the copied chain; each is matched to the child of the last original
with the same `created_at` — a copy keeps it, and nothing changes it —
and the cut is the last original matched. The one place a timestamp
decides anything, against the schema's audit-only rule: the fork kept
nothing else, and nothing reads it for this again. Two siblings alike to
the second end the walk at their parent — a shorter shared part, never a
wrong one. Forks are taken by source, each source read once.
"""

import re
import sqlite3

# The `stories` DDL exactly as each version shipped it. V11 is the step's
# precondition (what step 6 wrote, unchanged through 11); V12 is what it
# writes.
_V11_STORIES = """CREATE TABLE stories (
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

# What changes against V11: the `forked_from_message_id` column appended,
# the names realigned to it, `forked_from_id`'s comment.
_V12_STORIES = """CREATE TABLE stories (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    forked_from_id         INTEGER,      -- the story it was forked from; deliberately NOT a FK
    head_id                INTEGER,      -- current position in the message tree
    title                  BLOB,
    system                 BLOB,         -- the story's system prompt
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    settings               BLOB,         -- the story's settings as JSON, sealed; NULL when none
    forked_from_message_id INTEGER,      -- the last message it copied from that story; NOT a FK either
    FOREIGN KEY (id, head_id) REFERENCES messages(story_id, id)
)"""


def to_12(conn: sqlite3.Connection) -> None:
    _rewrite(conn, "stories", _V11_STORIES, _V12_STORIES)
    # fmt: off
    forks = conn.execute("SELECT id, forked_from_id FROM stories WHERE forked_from_id IS NOT NULL ORDER BY forked_from_id").fetchall()
    # fmt: on
    originals: dict[tuple[int | None, str], int | None] = {}
    read = None
    for story_id, source_id in forks:
        if source_id != read:
            originals, read = _originals(conn, source_id), source_id
        cut = _cut(conn, originals, story_id)
        if cut is not None:
            # fmt: off
            conn.execute("UPDATE stories SET forked_from_message_id = ? WHERE id = ?", (cut, story_id))
            # fmt: on


def _originals(conn: sqlite3.Connection, source: int) -> dict[tuple[int | None, str], int | None]:
    """A source's messages by their parent and `created_at`: None where
    two siblings share both, since no copy can tell them apart."""
    found: dict[tuple[int | None, str], int | None] = {}
    # fmt: off
    rows = conn.execute("SELECT id, parent_id, created_at FROM messages WHERE story_id = ?", (source,))
    # fmt: on
    for message_id, parent, created in rows:
        key = (parent, created)
        found[key] = None if key in found else message_id
    return found


def _cut(
    conn: sqlite3.Connection, originals: dict[tuple[int | None, str], int | None], fork: int
) -> int | None:
    """The last of the source's messages that `fork` holds a copy of; None
    where not even a root matches — the source deleted."""
    original: int | None = None
    copy: int | None = None
    # fmt: off
    rows = conn.execute("SELECT id, parent_id, created_at FROM messages WHERE story_id = ? ORDER BY id", (fork,))
    # fmt: on
    for message_id, parent, created in rows:
        # past the copies, the next row hangs off something else or matches nothing
        if parent != copy or (found := originals.get((original, created))) is None:
            break
        original, copy = found, message_id
    return original


def _rewrite(conn: sqlite3.Connection, table: str, expect: str, write: str) -> None:
    """One table's stored DDL replaced under `writable_schema`, refused
    unless it matches what the step expects — a hand-edited schema is
    never guessed at. Step 5's procedure, plus the schema-cookie bump
    step 7 added, so the connection rereads what it wrote — the update
    above writes the new column."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    if row is None or _normalized(row[0]) != _normalized(expect):
        raise sqlite3.DatabaseError(f"{table} DDL is not what schema version 11 shipped")
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
