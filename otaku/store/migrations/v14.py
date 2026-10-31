"""Schema step 14: what a version-13 database becomes.

One change, on `messages`, for three features at once: `kind` admits
'chapter', the row that opens a chapter; `pinned` is appended, the
reader's mark on a row sent whole even where its scene is summarized;
`word_count` is appended. The constraint widened and the columns
appended after the last column and before the table constraints, the
stored DDL rewritten by step 5's procedure, its helpers copied here
because a step's helpers are frozen with it. The record format is
positional, so existing rows read the defaults there: none is pinned,
and none is counted.

Then the rows are counted, on a best-effort basis: a database that is
not encrypted holds its bodies as their UTF-8 bytes, so every row is
counted here — a tool's result as none; an encrypted one stays
uncounted, since a step never holds the cipher.
"""

import base64
import re
import sqlite3

# `meta.check` as an unencrypted database stores it: the canary
# (`store.database`) as its bare bytes, base64.
_PLAIN_CHECK = base64.b64encode(b"otaku").decode()
# Chinese and Japanese leave no spaces between words, so each character
# there counts as one, as word processors count them: kana and the CJK
# ideographs. Korean spaces its words and is counted as they stand.
_CHARACTER_WORDS = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]")
# A word is a run between spaces holding a letter or a digit, so a lone
# dash or asterisk counts as none.
_WORD_CHARACTER = re.compile(r"[^\W_]")

# The `messages` DDL exactly as each version shipped it. V13 is the
# step's precondition (what version 13 wrote, unchanged since version 8);
# V14 is what it writes.
_V13_MESSAGES = """CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    parent_id   INTEGER,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant','tool')),
    kind        TEXT NOT NULL DEFAULT 'dialogue' CHECK (kind IN ('dialogue','narration','ooc','card','tool')),
    speaker_id  INTEGER REFERENCES characters(id) ON DELETE SET NULL,  -- extracted automatically
    speaker     BLOB,                    -- extracted automatically; name-at-the-time snapshot
    body        BLOB NOT NULL,           -- exactly what was typed/generated
    template    BLOB,                    -- the template the turn was played with, filled at wire time
    provider    TEXT,                    -- who generated it: an engine, 'card', or a tool's name
    model       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    attachments TEXT,                    -- the turn's pictures as a JSON list, see Attachment; NULL when none
    mode        TEXT NOT NULL DEFAULT 'chat' CHECK (mode IN ('story','chat')),  -- the mode it was written in
    UNIQUE (story_id, id),               -- composite-FK target: same-story references only
    FOREIGN KEY (story_id, parent_id) REFERENCES messages(story_id, id),
    CHECK (parent_id IS NULL OR parent_id < id)
)"""

# What changes against V13: 'chapter' among the kinds, `pinned` and
# `word_count` appended.
_V14_MESSAGES = """CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    parent_id   INTEGER,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant','tool')),
    kind        TEXT NOT NULL DEFAULT 'dialogue' CHECK (kind IN ('dialogue','narration','ooc','card','tool','chapter')),
    speaker_id  INTEGER REFERENCES characters(id) ON DELETE SET NULL,  -- extracted automatically
    speaker     BLOB,                    -- extracted automatically; name-at-the-time snapshot
    body        BLOB NOT NULL,           -- exactly what was typed/generated
    template    BLOB,                    -- the template the turn was played with, filled at wire time
    provider    TEXT,                    -- who generated it: an engine, 'card', or a tool's name
    model       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    attachments TEXT,                    -- the turn's pictures as a JSON list, see Attachment; NULL when none
    mode        TEXT NOT NULL DEFAULT 'chat' CHECK (mode IN ('story','chat')),  -- the mode it was written in
    pinned      BOOLEAN NOT NULL DEFAULT 0 CHECK (pinned IN (0,1)),  -- sent whole even where its scene is summarized
    word_count  INTEGER,                 -- NULL where it could not be counted
    UNIQUE (story_id, id),               -- composite-FK target: same-story references only
    FOREIGN KEY (story_id, parent_id) REFERENCES messages(story_id, id),
    CHECK (parent_id IS NULL OR parent_id < id)
)"""


def to_14(conn: sqlite3.Connection) -> None:
    _rewrite(conn, "messages", _V13_MESSAGES, _V14_MESSAGES)
    row = conn.execute("SELECT value FROM meta WHERE key = 'check'").fetchone()
    if row is None or row[0] != _PLAIN_CHECK:
        return  # encrypted, or no canary to judge by: left uncounted
    # Only the counts are held, never the bodies, so a large story costs
    # no memory to speak of.
    counts = [
        (0 if role == "tool" else _count_words(bytes(body).decode("utf-8", "replace")), mid)
        for mid, role, body in conn.execute("SELECT id, role, body FROM messages")
    ]
    conn.executemany("UPDATE messages SET word_count = ? WHERE id = ?", counts)


def _rewrite(conn: sqlite3.Connection, table: str, expect: str, write: str) -> None:
    """One table's stored DDL replaced under `writable_schema`, refused
    unless it matches what the step expects — a hand-edited schema is
    never guessed at. Step 5's procedure, plus the schema-cookie bump
    step 7 added, so the connection rereads what it wrote."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    if row is None or _normalized(row[0]) != _normalized(expect):
        raise sqlite3.DatabaseError(f"{table} DDL is not what schema version 13 shipped")
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


def _count_words(text: str) -> int:
    """The words of a body as stored: each Chinese or Japanese character,
    then every run between spaces that holds a letter or a digit."""
    characters = len(_CHARACTER_WORDS.findall(text))
    rest = _CHARACTER_WORDS.sub(" ", text)
    return characters + sum(1 for run in rest.split() if _WORD_CHARACTER.search(run))
