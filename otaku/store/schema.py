"""The data model: the DDL, its semantics, and the row types.

Sections: stories/messages are *source* (what was said),
scenes/characters/journals are *derivatives* (memory distilled from the
source, rebuildable from it); the rest is bookkeeping.

Semantics:

- Story = chat = playthrough. Forking a story deep-copies everything —
  messages, scenes, journals, characters (ids remapped) — so every story is
  fully self-contained and deletion is one cascade. `forked_from_id` is
  audit-only lineage (deliberately not a FK). The cast is per-story.
- Messages form a sibling tree via `parent_id`: undo moves `head_id` back,
  regenerate diverges into a sibling; undone and regenerated messages are
  never deleted. Message rows are never rewritten by the app — the author's
  own edit through the UI is the one deliberate exception.
- Scenes only exist closed: one INSERT writes start, end, title, summary.
  On fork, a scene cut mid-span is not copied — its messages count as
  unextracted tail in the new story.
- Two-level rollup pattern, identical in scenes and journals:
  `scenes.summary` / `journals.entry` hold this scene only and are
  append-only; `scenes.history` (the story so far THROUGH this scene) and
  `journals.history` (the character's cumulative memory) are rollups —
  always composed from the per-scene records, never from a previous rollup,
  riding on the row they were generated at. The latest non-NULL history
  plus the records after it cover the whole story with no gap and no
  overlap; editing a per-scene record nulls the rollups composed from it.
- Encryption is off by default, and BLOB columns then hold readable plain
  text. With it on, every BLOB column holds ONE value sealed by the cipher.
  Ids, topology, enums, provider/model names, and timestamps are plaintext
  either way — the store queries on them. `meta.check` holds a known
  plaintext sealed by the database's cipher; it must unseal correctly at
  open, which catches a mode mismatch with [encryption] and a wrong or
  replaced key alike.
- Timestamps are app-written local time with offset (ISO-8601) and are
  audit-only: no business logic relies on them; UI display use (the story
  list's recency ordering) is allowed.
"""

from dataclasses import dataclass

SCHEMA_VERSION = "1"

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
                  CHECK (kind IN ('dialogue','narration','ooc')),
    speaker_id  INTEGER REFERENCES characters(id) ON DELETE SET NULL,  -- extracted automatically
    speaker     BLOB,                    -- extracted automatically; name-at-the-time snapshot
    body        BLOB NOT NULL,           -- exactly what was typed/generated
    framing     BLOB,                    -- /me /you /ooc injection, joined to body at wire time
    provider    TEXT,                    -- who generated an assistant turn
    model       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
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
    UNIQUE (story_id, start_message_id),
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
    updated_at  TEXT NOT NULL
);

CREATE TABLE journals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id     INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    scene_id     INTEGER NOT NULL,
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    entry        BLOB NOT NULL,          -- their record of this scene only
    state        BLOB NOT NULL,          -- snapshot right now; latest row wins
    history      BLOB,                   -- cumulative rollup from their entries
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (scene_id, character_id),
    FOREIGN KEY (story_id, scene_id) REFERENCES scenes(story_id, id) ON DELETE CASCADE
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
    created_at        TEXT NOT NULL
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
class Message:
    """One turn of a story. `id` is 0 on a turn not yet stored; `append`
    assigns the real one."""

    role: str  # 'user' | 'assistant'
    body: str
    kind: str = "dialogue"  # 'dialogue' | 'narration' | 'ooc'
    framing: str | None = None  # joined to body at wire time, never mixed into it
    speaker: str | None = None
    speaker_id: int | None = None
    provider: str | None = None  # set on assistant turns
    model: str | None = None
    id: int = 0


@dataclass(frozen=True)
class Scene:
    id: int
    start_message_id: int
    end_message_id: int
    title: str = ""
    summary: str = ""
    history: str = ""  # story-so-far through this scene; "" when not generated here


@dataclass(frozen=True)
class Character:
    id: int
    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class Journal:
    """One journal row: a character's record of one scene."""

    id: int
    scene_id: int
    character_id: int
    entry: str
    state: str
    history: str = ""
