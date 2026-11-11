"""The data model's tables: the DDL, its semantics, and a row type for
each table. What a row carries beyond its scalars is beside it: its
pictures (`store.pictures`), and a story's settings and the modes its
layout derives (`store.story`).

Always the CURRENT shape: a fresh database is created from it directly,
and `store.migrations` brings old databases to it. The semantics ride in
from the old module unchanged with the DDL text: cards the codex, which
no story owns, stories/messages source, scenes/characters/journals
derivatives, sibling trees via parent_id, the two-level rollup pattern,
per-field sealing (`attachments` and `pictures` plain on purpose: the
app's facts about files the folder beside the database holds sealed;
`digests` too, the folder's own judgement on an unkeyed digest),
audit-only timestamps. A story's settings are one sealed JSON on its row
(`StorySettingDB`); the `settings` table holds the ones stories SHARE. A
setting is that table's when it is a text that may need sealing — a
config file is never sealed — and a config file's otherwise.
"""

from dataclasses import dataclass

from otaku.store.pictures import Attachment, CardPicture, CardPictureKind
from otaku.store.story import StoryMode, StorySettingDB

SCHEMA_VERSION = "15"

SCHEMA_DDL = """
-- ---------- the codex: what every story may draw on ----------

-- A story's character links to its card (`characters.card_id`) and holds
-- a COPY of its profile: an edit on either side never reaches the other.
CREATE TABLE cards (
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
);

-- ---------- source: what was actually said ----------

CREATE TABLE stories (
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
);

-- Undone and regenerated messages are never deleted: the story's head_id
-- moves instead, and abandoned turns remain in the tree as siblings.
CREATE TABLE messages (
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
);

-- ---------- derivatives: memory distilled from the source ----------

-- `provider` and `model` on the three tables name who MADE the row, as on
-- messages: the pass that wrote it (its engine and model), or 'card' and
-- the card's file for an imported character. An edit or a later rollup
-- never changes them; NULL where no pass made the row, or before v9.

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
    provider         TEXT,               -- who made the row
    model            TEXT,
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
    profile     BLOB,                    -- a copy of its card's profile as TOML, with the names it binds; NULL for an extracted one
    provider    TEXT,                    -- who made the row
    model       TEXT,
    card_id     INTEGER REFERENCES cards(id) ON DELETE SET NULL,  -- the codex card it is linked to
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
    provider     TEXT,                   -- who made the row
    model        TEXT,
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
CREATE UNIQUE INDEX idx_characters_card ON characters (card_id, story_id) WHERE card_id IS NOT NULL;
CREATE INDEX idx_journals_rollup   ON journals (story_id, character_id, id);
CREATE INDEX idx_token_usage_story ON token_usage (story_id);
"""


@dataclass(frozen=True)
class Card:
    """A character card in the codex: a character as the reader keeps it
    across stories. `profile` is the card's text as the TOML
    `context.cards` writes and reads; its pictures are its portraits
    first, the first the main one, then whatever else its card file
    brought, each of its kind; `digests` name the card files it was made
    from, and `original_file` the one it was imported from, kept as it
    came: its name in the files folder (None for none)."""

    id: int
    name: str
    aliases: tuple[str, ...] = ()
    tagline: str = ""
    profile: str = ""
    pictures: tuple[CardPicture, ...] = ()  # the files folder holds the bytes
    digests: tuple[str, ...] = ()
    original_file: str | None = None
    updated_at: str = ""  # audit column, surfaced for display alone

    @property
    def portraits(self) -> tuple[CardPicture, ...]:
        """The pictures of the character itself, in order, the first the
        main one — what every story and both frontends show of a card."""
        return tuple(
            picture for picture in self.pictures if picture.kind is CardPictureKind.PORTRAIT
        )


@dataclass(frozen=True)
class Story:
    id: int
    title: str
    system: str
    head_id: int | None
    forked_from_id: int | None
    forked_from_message_id: int | None  # the cut, in the story forked from
    settings: tuple[StorySettingDB, ...] = ()  # what the story has stored, see StorySettingDB


@dataclass(frozen=True, kw_only=True)
class Message:
    """One turn of a story. A chapter's row opens a chapter, its body the
    `/chapter` line; a tool's row is the result of a tool otaku ran —
    nobody's line, sent as the reader's turn, framed out of character."""

    id: int = 0  # until stored
    role: str  # 'user' | 'assistant' | 'tool'
    kind: str = "dialogue"  # 'dialogue' | 'narration' | 'ooc' | 'card' | 'tool' | 'chapter'
    speaker_id: int | None = None
    speaker: str | None = None
    body: str
    template: str | None = None  # filled at wire time, never mixed into the body
    # who made it: an engine, 'card', a tool's name; None on the reader's own
    provider: str | None = None
    model: str | None = None
    attachments: tuple[Attachment, ...] = ()  # the files folder holds the bytes
    # the layout's mode it was recorded in, read by the join rule; a
    mode: StoryMode = StoryMode.CHAT
    pinned: bool = False  # the reader's mark: sent whole even where its scene is summarized

    # ---- derived: counted when the row is read, never stored ----

    # the chapter it stands in along its chain, 0 before the first; None
    # on a row read alone
    chapter: int | None = None

    @property
    def is_scene_start(self) -> bool:
        """Whether a scene of the lore must begin at this row: a chapter's,
        which no scene crosses."""
        return self.kind == "chapter"


@dataclass(frozen=True)
class Scene:
    id: int
    start_message_id: int
    end_message_id: int
    title: str = ""
    summary: str = ""
    history: str = ""  # story-so-far through this scene; "" when not generated here
    updated_at: str = ""  # audit column, surfaced for display alone ("extracted 4m ago")
    provider: str | None = None  # who made the row (see the DDL)
    model: str | None = None


@dataclass(frozen=True)
class Character:
    id: int
    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    profile: str | None = None  # its card's profile, a copy; None for extracted characters
    updated_at: str = ""  # audit column, surfaced for display alone
    provider: str | None = None  # who made the row (see the DDL)
    model: str | None = None
    card_id: int | None = None  # the codex card it is linked to; None when unlinked


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
    provider: str | None = None  # who made the row (see the DDL)
    model: str | None = None
