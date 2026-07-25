# otaku schema — v1

## Semantics

- **Story = playthrough.** Forking a story deep-copies everything — messages,
  scenes, journals, characters (ids remapped) — so every story is fully
  self-contained and deletion is one cascade. `forked_from_id` is audit-only
  lineage (deliberately not a FK). The cast is per-story.
- **Message tree.** `parent_id` makes a sibling tree inside the story: undo moves
  `head_id` back, regenerate diverges into a sibling; message rows are never
  rewritten by the app (author edits via the UI are the one deliberate exception).
- **Scenes only exist closed.** One INSERT writes start, end, title, summary.
  On fork, a scene cut mid-span is not copied — its messages count as untriaged
  tail in the new story.
- **Two-level rollup pattern**, identical in scenes and journals:
  - `scenes.summary` / `journals.entry` — this scene only, append-only.
  - `scenes.history` — the story so far THROUGH this scene, composed from the
    scene summaries (never from a previous history), riding on the row it was
    generated at. The current story arc = the latest non-NULL `history` among
    on-path scenes; the row it sits on is its coverage stamp. Editing a summary
    nulls histories at-or-after that scene.
  - `journals.history` — the character's cumulative rollup, composed from their
    own entries the same way. `journals.state` is a snapshot; the latest row wins.
- **Merging characters** rewrites references (message speakers, journals) to the
  target, folds the source name into the target's aliases, and deletes the
  source row.
- **Encryption boundary.** Every BLOB column holds content sealed by the cipher
  (one opaque value; readable plaintext under encryption provider "none"). Ids,
  topology, enums, provider/model names, and timestamps are plaintext — the
  store queries on them.
- **Timestamps** are app-written local time with offset (ISO-8601) and are
  audit-only. One sanctioned exception: `stories.updated_at` may drive the story
  browser's most-recently-played-first ordering (UI display only, never logic).
  Every updatable table carries both `created_at` and `updated_at`.
- The `migration/` folder at the repo root holds a one-time standalone
  conversion script for a pre-existing database; it is never packaged with the
  app, which opens only this schema (`meta.schema_version = '1'`).

## DDL

```sql
-- ---------- source: what was actually said ----------

CREATE TABLE stories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    forked_from_id INTEGER,              -- audit: forked from which story; deliberately NOT a FK
    head_id        INTEGER,              -- current position in the message tree
    title          BLOB,
    system         BLOB,                 -- the story's system prompt
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,        -- audit + story-list display ordering ONLY
    FOREIGN KEY (id, head_id) REFERENCES messages(story_id, id)
);

CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    parent_id   INTEGER,                 -- sibling tree: undo/regen diverge, nothing is rewritten
    role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
    kind        TEXT NOT NULL DEFAULT 'dialogue'
                  CHECK (kind IN ('dialogue','narration','ooc')),
    speaker_id  INTEGER REFERENCES characters(id) ON DELETE SET NULL,
    speaker     BLOB,                    -- name-at-the-time snapshot beside the link
    body        BLOB NOT NULL,           -- exactly what was typed/generated
    framing     BLOB,                    -- /me /you /ooc injection, joined to body at wire time
    provider    TEXT,                    -- who generated an assistant turn
    model       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (story_id, id),
    FOREIGN KEY (story_id, parent_id) REFERENCES messages(story_id, id),
    CHECK (parent_id IS NULL OR parent_id < id)
);

-- ---------- derivatives: memory distilled from the source ----------

CREATE TABLE scenes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id         INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    start_message_id INTEGER NOT NULL,
    end_message_id   INTEGER NOT NULL,   -- scenes only exist closed
    title            BLOB,
    summary          BLOB,               -- this scene only; append-only
    history          BLOB,               -- story-so-far THROUGH this scene; latest non-NULL = the arc
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (story_id, id),
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
    story_id          INTEGER REFERENCES stories(id) ON DELETE SET NULL,  -- accounting survives deletion
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    purpose           TEXT NOT NULL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    duration_seconds  REAL,
    created_at        TEXT NOT NULL
);

CREATE TABLE prompt_history (            -- the REPL's Up/Down input history, capped
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    body       BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- schema_version = '1'

CREATE INDEX idx_messages_story    ON messages (story_id);
CREATE INDEX idx_messages_parent   ON messages (parent_id);
CREATE INDEX idx_messages_speaker  ON messages (speaker_id);
CREATE INDEX idx_scenes_story      ON scenes (story_id);
CREATE INDEX idx_scenes_end        ON scenes (end_message_id);
CREATE INDEX idx_characters_story  ON characters (story_id);
CREATE INDEX idx_journals_rollup   ON journals (story_id, character_id, id);
CREATE INDEX idx_token_usage_story ON token_usage (story_id);
```
