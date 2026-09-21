"""The data model: the DDL, its semantics, and the row types.

Always the CURRENT shape: a fresh database is created from it directly,
and `store.migrations` brings old databases to it. The semantics ride in
from the old module unchanged with the DDL text: stories/messages are
source, scenes/characters/journals derivatives, sibling trees via
parent_id, the two-level rollup pattern, per-field sealing (the `attachments`
column plain on purpose: the app's facts about files the folder beside
the database holds sealed), audit-only timestamps. `globals` is the one
table of CONTENT that belongs to no story: what the user wrote to be
played in every one (a setting is a config file's, never this table's).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

SCHEMA_VERSION = "6"

SCHEMA_DDL = """
-- ---------- source: what was actually said ----------

CREATE TABLE stories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    forked_from_id INTEGER,              -- audit: forked from which story; deliberately NOT a FK
    head_id        INTEGER,              -- current position in the message tree
    title          BLOB,
    system         BLOB,                 -- the story's system prompt
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    settings       BLOB,                 -- the story's settings as JSON, sealed; NULL when none
    FOREIGN KEY (id, head_id) REFERENCES messages(story_id, id)
);

-- Undone and regenerated messages are never deleted: the story's head_id
-- moves instead, and abandoned turns remain in the tree as siblings.
CREATE TABLE messages (
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
);

-- ---------- derivatives: memory distilled from the source ----------

CREATE TABLE scenes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id         INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    start_message_id INTEGER NOT NULL,
    end_message_id   INTEGER NOT NULL,
    title            BLOB,
    summary          BLOB,               -- this scene only; append-only
    history          BLOB,               -- story-so-far THROUGH this scene; latest = the arc
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (story_id, id),               -- composite-FK target: same-story references only
    FOREIGN KEY (story_id, start_message_id) REFERENCES messages(story_id, id),
    FOREIGN KEY (story_id, end_message_id)   REFERENCES messages(story_id, id)
);

CREATE TABLE characters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    name        BLOB NOT NULL,
    aliases     BLOB,                    -- JSON array, sealed
    description BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    card        BLOB,                    -- an imported card as TOML
    UNIQUE (story_id, id)                -- composite-FK target: same-story references only
);

CREATE TABLE journals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id     INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    scene_id     INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    entry        BLOB NOT NULL,          -- their record of this scene only
    state        BLOB NOT NULL,          -- snapshot right now; latest row wins
    history      BLOB,                   -- cumulative rollup from their entries
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (scene_id, character_id),
    FOREIGN KEY (story_id, scene_id) REFERENCES scenes(story_id, id) ON DELETE CASCADE,
    FOREIGN KEY (story_id, character_id) REFERENCES characters(story_id, id) ON DELETE CASCADE
);

-- ---------- the user's own, outside any story ----------

CREATE TABLE globals (                   -- content that belongs to every story, sealed
    key        TEXT PRIMARY KEY,         -- 'reminder'
    value      BLOB NOT NULL,            -- sealed; an emptied value deletes its row
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ---------- bookkeeping ----------

CREATE TABLE token_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id          INTEGER REFERENCES stories(id) ON DELETE SET NULL,  -- survives deletion
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    purpose           TEXT NOT NULL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    duration_seconds  REAL,
    created_at        TEXT NOT NULL,
    cached_tokens     INTEGER             -- of prompt_tokens, served from the provider's cache
);

CREATE TABLE history (                   -- the REPL's Up/Down input history, capped
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    body       BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- schema_version, check

CREATE INDEX idx_messages_story    ON messages (story_id);
CREATE INDEX idx_messages_parent   ON messages (parent_id);
CREATE INDEX idx_messages_speaker  ON messages (speaker_id);
CREATE INDEX idx_scenes_story      ON scenes (story_id);
CREATE INDEX idx_scenes_end        ON scenes (end_message_id);
CREATE INDEX idx_characters_story  ON characters (story_id);
CREATE INDEX idx_journals_rollup   ON journals (story_id, character_id, id);
CREATE INDEX idx_token_usage_story ON token_usage (story_id);
"""


@dataclass(frozen=True)
class Story:
    id: int
    title: str
    system: str
    head_id: int | None
    forked_from_id: int | None


@dataclass(frozen=True)
class Attachment:
    """One picture on a turn, as the `attachments` column records it: the
    app's facts about a file in the folder beside the database, none of
    the reader's words — which is why the column is plain where the file
    is sealed. `file` is the file's name in the folder, extension
    included (`store.files` draws it; the extension says the type), the
    rest their measure as the model sees them — recorded here so that no
    reader has to open a sealed file to describe a picture. The reader's
    original file name is not kept: nothing needs it once the picture
    is in."""

    file: str
    width: int
    height: int
    size: int


@dataclass(frozen=True)
class Message:
    """One turn of a story. `id` is 0 on a turn not yet stored; `append`
    assigns the real one. `kind` is 'dialogue' | 'narration' | 'ooc' |
    'card' — every kind is a STORED kind; what only exists on the wire
    is `context.assembler.WireTurn`, a different type."""

    role: str  # 'user' | 'assistant'
    body: str
    kind: str = "dialogue"
    template: str | None = None  # filled at wire time, never mixed into the body
    speaker: str | None = None
    speaker_id: int | None = None
    provider: str | None = None  # set on assistant turns ('card' on a card greeting)
    model: str | None = None
    attachments: tuple[
        Attachment, ...
    ] = ()  # the turn's pictures; the files folder holds the bytes
    id: int = 0


@dataclass(frozen=True)
class Scene:
    id: int
    start_message_id: int
    end_message_id: int
    title: str = ""
    summary: str = ""
    history: str = ""  # story-so-far through this scene; "" when not generated here
    updated_at: str = ""  # audit column, surfaced for display alone ("extracted 4m ago")


@dataclass(frozen=True)
class Character:
    id: int
    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    card: str | None = None  # the import archive; None for extracted characters
    updated_at: str = ""  # audit column, surfaced for display alone


@dataclass(frozen=True)
class Journal:
    """One journal row: a character's record of one scene."""

    id: int
    scene_id: int
    character_id: int
    entry: str
    state: str
    history: str = ""
    updated_at: str = ""  # audit column, surfaced for display alone


def attachments_to_json(attachments: Sequence[Attachment]) -> str | None:
    """The column's text for these pictures: a JSON list, or NULL (None)
    for a turn without any — the column never holds an empty list
    pretending to be absent. The keys are a contract with the SQL that
    reads the column back (`$.file` is what the sweep extracts)."""
    if not attachments:
        return None
    return json.dumps(
        [
            {
                "file": a.file,
                "width": a.width,
                "height": a.height,
                "size": a.size,
            }
            for a in attachments
        ]
    )


def attachments_from_json(text: str | None) -> tuple[Attachment, ...]:
    """The column read back; NULL → no pictures, which is also what a
    row written before the column existed reads as."""
    if not text:
        return ()
    return tuple(
        Attachment(
            file=str(item["file"]),
            width=int(item["width"]),
            height=int(item["height"]),
            size=int(item["size"]),
        )
        for item in json.loads(text)
    )
