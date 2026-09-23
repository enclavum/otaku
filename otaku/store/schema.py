"""The data model: the DDL, its semantics, and the row types.

Always the CURRENT shape: a fresh database is created from it directly,
and `store.migrations` brings old databases to it. The semantics ride in
from the old module unchanged with the DDL text: stories/messages are
source, scenes/characters/journals derivatives, sibling trees via
parent_id, the two-level rollup pattern, per-field sealing (the `attachments`
column plain on purpose: the app's facts about files the folder beside
the database holds sealed), audit-only timestamps. A story's settings
are one sealed JSON on its row (`StorySettingDB`); the `settings` table
holds the ones stories SHARE. A setting is that table's when it is a
text that may need sealing — a config file is never sealed — and a
config file's otherwise.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Self

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

CREATE TABLE settings (                  -- settings that stories share; the texts that may need sealing
    key        TEXT PRIMARY KEY,         -- 'shared_reminder'
    value      BLOB NOT NULL,            -- sealed; an emptied value deletes its row
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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


# Where what a setting injects rides (`context.injections.Injection`):
# "system", or which of the reader's messages it goes BEFORE, counted
# from the end — 1 is the newest, 2 the one before it.
InjectionPosition = Literal["system"] | int


@dataclass(frozen=True)
class StorySettingDB:
    """One setting of a story, as the `settings` column records it: a
    switch, where what it injects rides, the story's own reminder, and
    whether the reader is shown the notes the model writes — the last two
    each one setting's, the others every injecting setting's.
    WHICH settings there are, and what each may take, is no business of
    the column's (`backend.story`)."""

    name: str
    enabled: bool = False
    position: InjectionPosition | None = None  # None: unsaid, the setting's default applies
    reminder_text: str = ""
    display_notes: bool = True

    @classmethod
    def from_json(cls, text: str) -> tuple[Self, ...]:
        """The column read back: every setting it holds, in its order.
        What makes no sense reads as unsaid — `enabled` that is not `true`
        is False, a position that is not "system" or a count from 1 is
        None — and a text that is no JSON object of objects holds none."""
        out = []
        for name, state in cls._parse(text).items():
            if not isinstance(state, dict):
                continue
            position, reminder_text = state.get("position"), state.get("reminder_text")
            # By type first: to Python True is 1 and 1.0 is 1, and neither is a position.
            placed = position == "system" or (type(position) is int and position >= 1)
            out.append(
                cls(
                    name=name,
                    enabled=state.get("enabled") is True,
                    position=position if placed else None,
                    reminder_text=reminder_text if isinstance(reminder_text, str) else "",
                    display_notes=state.get("display_notes") is not False,
                )
            )
        return tuple(out)

    @classmethod
    def to_json(cls, settings: Sequence[Self], current: str = "") -> str:
        """The column's text with these settings in it — a MERGE over
        `current`, never a rewrite: a JSON key bumps no schema version, so
        an older build can meet a newer one's keys and must hand them back
        whole. Only what is said is written; an emptied reminder leaves
        the column."""
        column = cls._parse(current)
        for setting in settings:
            state = column.get(setting.name)
            state = dict(state) if isinstance(state, dict) else {}
            state["enabled"] = setting.enabled
            if setting.position is not None:
                state["position"] = setting.position
            if setting.reminder_text:
                state["reminder_text"] = setting.reminder_text
            else:
                state.pop("reminder_text", None)
            if setting.display_notes:
                state.pop("display_notes", None)  # the default; only the exception is said
            else:
                state["display_notes"] = False
            column[setting.name] = state
        return json.dumps(column, ensure_ascii=False)

    @staticmethod
    def _parse(text: str) -> dict[str, object]:
        """The column's text parsed: the JSON object it holds, or an empty
        dict for anything else — nothing, garbage, a list."""
        try:
            parsed = json.loads(text) if text else {}
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class Story:
    id: int
    title: str
    system: str
    head_id: int | None
    forked_from_id: int | None
    settings: tuple[StorySettingDB, ...] = ()  # what the story has stored, see StorySettingDB


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

    @classmethod
    def from_json(cls, text: str | None) -> tuple[Self, ...]:
        """The column read back; NULL → no pictures, which is also what a
        row written before the column existed reads as."""
        if not text:
            return ()
        return tuple(
            cls(
                file=str(item["file"]),
                width=int(item["width"]),
                height=int(item["height"]),
                size=int(item["size"]),
            )
            for item in json.loads(text)
        )

    @classmethod
    def to_json(cls, attachments: Sequence[Self]) -> str | None:
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
