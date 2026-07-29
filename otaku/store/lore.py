"""The derivative memory: scenes, cast, journals — one ops class per table.

Everything here is distilled from the source by lore extraction and can be
rebuilt from it. The write disciplines are part of the data model (see
`store.schema`); each edit writer below carries its own invalidation, so
correcting a per-scene record nulls the rollups composed from it and the
next pass rebuilds them.
"""

import builtins
import json
from dataclasses import dataclass

from otaku.store.database import Database
from otaku.store.schema import Character, Journal, Scene

# Journal rows this character's current history rollup does not cover yet.
# One predicate shared by every reader, so they can never disagree about
# where the uncovered entries begin.
_UNCOVERED = (
    "j.story_id = ? AND j.id > COALESCE((SELECT MAX(h.id) FROM journals h "
    "WHERE h.story_id = j.story_id AND h.character_id = j.character_id "
    "AND h.history IS NOT NULL), 0)"
)


class SceneOps:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        story_id: int,
        *,
        start_message_id: int,
        end_message_id: int,
        title: str | None = None,
        summary: str | None = None,
        history: str | None = None,
    ) -> int:
        """One INSERT writes the whole scene."""
        now = self._db.now()
        with self._db.conn as conn:
            # fmt: off
            cur = conn.execute(
                "INSERT INTO scenes (story_id, start_message_id, end_message_id, title, summary, history, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (story_id, start_message_id, end_message_id, self._db.seal_opt(title), self._db.seal_opt(summary), self._db.seal_opt(history), now, now),
            )
            # fmt: on
        return int(cur.lastrowid or 0)

    def list(self, story_id: int) -> builtins.list[Scene]:
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT id, start_message_id, end_message_id, title, summary, history FROM scenes WHERE story_id = ? ORDER BY id",
            (story_id,),
        ).fetchall()
        # fmt: on
        return [
            Scene(
                id=int(sid),
                start_message_id=int(start),
                end_message_id=int(end),
                title=self._db.unseal(title),
                summary=self._db.unseal(summary),
                history=self._db.unseal(history),
            )
            for sid, start, end, title, summary, history in rows
        ]

    def update(
        self, scene_id: int, *, title: str | None = None, summary: str | None = None
    ) -> None:
        """The author's correction of a scene. None leaves a field as it is;
        "" clears it. Editing the summary nulls the history rollups at or
        after this scene — they were composed from the old text — and the
        next pass rebuilds them from the corrected record."""
        row = self._db.conn.execute(
            "SELECT story_id FROM scenes WHERE id = ?", (scene_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no scene {scene_id}")
        story_id = int(row[0])
        now = self._db.now()
        with self._db.conn as conn:
            # fmt: off
            if title is not None:
                conn.execute(
                    "UPDATE scenes SET title = ?, updated_at = ? WHERE id = ?",
                    (self._db.seal_opt(title or None), now, scene_id),
                )
            if summary is not None:
                conn.execute(
                    "UPDATE scenes SET summary = ?, updated_at = ? WHERE id = ?",
                    (self._db.seal_opt(summary or None), now, scene_id),
                )
                conn.execute(
                    "UPDATE scenes SET history = NULL, updated_at = ? WHERE story_id = ? AND id >= ?",
                    (now, story_id, scene_id),
                )
            # fmt: on

    # ---------- getters and setters ----------

    def get_current(self, story_id: int, message_ids: builtins.list[int]) -> builtins.list[Scene]:
        """The scenes of the story's current timeline: those whose end lies
        among `message_ids` (the story's messages as of now). A scene whose
        end was undone away describes a rewound continuation — it is left
        out everywhere. Decrypts every scene; callers needing only
        boundaries want `get_current_ends`."""
        current = set(message_ids)
        return [s for s in self.list(story_id) if s.end_message_id in current]

    def get_current_ends(
        self, story_id: int, message_ids: builtins.list[int]
    ) -> builtins.list[int]:
        """The end message of each current scene, oldest first — the same
        rule as `get_current` with nothing decrypted."""
        current = set(message_ids)
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT end_message_id FROM scenes WHERE story_id = ? ORDER BY id",
            (story_id,),
        ).fetchall()
        # fmt: on
        return [int(end) for (end,) in rows if end in current]

    def get_arc(self, story_id: int, message_ids: builtins.list[int]) -> str:
        """The story so far: the newest history rollup among current scenes."""
        for scene in reversed(self.get_current(story_id, message_ids)):
            if scene.history:
                return scene.history
        return ""

    def get_rollups_due(self, story_id: int, message_ids: builtins.list[int]) -> builtins.list[int]:
        """The scenes the next story-so-far rollups belong on: the newest
        current scene, when its history is missing — freshly closed, or
        invalidated by a summary edit (at most one today; the shape matches
        the journals' counterpart). Id-only; nothing is decrypted."""
        current = set(message_ids)
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT id, end_message_id, history IS NULL FROM scenes WHERE story_id = ? ORDER BY id",
            (story_id,),
        ).fetchall()
        # fmt: on
        live = [(int(sid), bool(missing)) for sid, end, missing in rows if end in current]
        if live and live[-1][1]:
            return [live[-1][0]]
        return []

    def set_history(self, scene_id: int, history: str) -> None:
        """Attach a freshly composed story-so-far rollup to the scene it was
        generated at."""
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE scenes SET history = ?, updated_at = ? WHERE id = ?",
                (self._db.seal(history), self._db.now(), scene_id),
            )
            # fmt: on


class CharacterOps:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        story_id: int,
        name: str,
        *,
        aliases: tuple[str, ...] = (),
        description: str | None = None,
    ) -> int:
        now = self._db.now()
        sealed_aliases = self._db.seal_opt(json.dumps(list(aliases)) if aliases else None)
        with self._db.conn as conn:
            # fmt: off
            cur = conn.execute(
                "INSERT INTO characters (story_id, name, aliases, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (story_id, self._db.seal(name), sealed_aliases, self._db.seal_opt(description), now, now),
            )
            # fmt: on
        return int(cur.lastrowid or 0)

    def list(self, story_id: int) -> builtins.list[Character]:
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT id, name, aliases, description FROM characters WHERE story_id = ? ORDER BY id",
            (story_id,),
        ).fetchall()
        # fmt: on
        return [
            Character(
                id=int(cid),
                name=self._db.unseal(name),
                aliases=self._decode_aliases(aliases),
                description=self._db.unseal(description),
            )
            for cid, name, aliases, description in rows
        ]

    def update(
        self,
        character_id: int,
        *,
        aliases: tuple[str, ...] = (),
        description: str | None = None,
    ) -> None:
        """Fill in details for a character created bare (a speaker label
        creates the row before the extraction's own entry arrives). Strictly
        additive: aliases merge, an existing description is never
        overwritten."""
        row = self._db.conn.execute(
            "SELECT aliases, description FROM characters WHERE id = ?", (character_id,)
        ).fetchone()
        if row is None:
            return
        merged = list(dict.fromkeys([*self._decode_aliases(row[0]), *aliases]))
        sealed_aliases = self._db.seal_opt(json.dumps(merged) if merged else None)
        kept_description = row[1] if row[1] is not None else self._db.seal_opt(description)
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE characters SET aliases = ?, description = ?, updated_at = ? WHERE id = ?",
                (sealed_aliases, kept_description, self._db.now(), character_id),
            )
            # fmt: on

    # ---------- getters and setters ----------

    def set_description(self, character_id: int, description: str) -> None:
        """The author's correction — unlike `update`, this replaces the
        existing text; "" clears it."""
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE characters SET description = ?, updated_at = ? WHERE id = ?",
                (self._db.seal_opt(description or None), self._db.now(), character_id),
            )
            # fmt: on

    # ---------- resolution and shaping ----------

    def find(self, story_id: int, name: str) -> Character | None:
        """Resolve a name or alias, case-insensitively — the one owner of
        name resolution."""
        wanted = name.strip().casefold()
        for character in self.list(story_id):
            if character.name.casefold() == wanted:
                return character
            if any(alias.casefold() == wanted for alias in character.aliases):
                return character
        return None

    def merge(self, story_id: int, source_id: int, target_id: int) -> None:
        """Fold a duplicate into the real one: every reference — message
        speakers, journal rows — moves to the target, the source's name and
        aliases join the target's aliases, and the source row is deleted.
        Where both wrote a journal row for the same scene, the target's row
        wins and the source's is dropped."""
        # fmt: off
        rows = {
            int(cid): (name, aliases)
            for cid, name, aliases in self._db.conn.execute(
                "SELECT id, name, aliases FROM characters WHERE story_id = ? AND id IN (?, ?)",
                (story_id, source_id, target_id),
            )
        }
        # fmt: on
        if source_id not in rows or target_id not in rows:
            raise ValueError("both characters must exist in this story")
        source_name = self._db.unseal(rows[source_id][0])
        merged = list(
            dict.fromkeys(
                [
                    *self._decode_aliases(rows[target_id][1]),
                    source_name,
                    *self._decode_aliases(rows[source_id][1]),
                ]
            )
        )
        now = self._db.now()
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE characters SET aliases = ?, updated_at = ? WHERE id = ?",
                (self._db.seal(json.dumps(merged)), now, target_id),
            )
            conn.execute(
                "UPDATE messages SET speaker_id = ? WHERE speaker_id = ?",
                (target_id, source_id),
            )
            conn.execute(
                "DELETE FROM journals WHERE character_id = ? AND scene_id IN (SELECT scene_id FROM journals WHERE character_id = ?)",
                (source_id, target_id),
            )
            conn.execute(
                "UPDATE journals SET character_id = ?, updated_at = ? WHERE character_id = ?",
                (target_id, now, source_id),
            )
            conn.execute(
                "DELETE FROM characters WHERE id = ?",
                (source_id,),
            )
            # fmt: on

    def _decode_aliases(self, sealed: bytes | None) -> tuple[str, ...]:
        raw = self._db.unseal(sealed)
        if not raw:
            return ()
        try:
            return tuple(str(alias) for alias in json.loads(raw))
        except json.JSONDecodeError, TypeError:
            return ()


@dataclass(frozen=True)
class CharacterMemory:
    """A character's memory as of now — an aggregate over their journal
    rows: the latest history rollup, the entries written after it (which it
    does not cover), and their latest state. Together, the whole story as
    they know it, with no gap and no overlap."""

    history: str = ""
    entries: tuple[str, ...] = ()
    state: str = ""


class JournalOps:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self, story_id: int, scene_id: int, character_id: int, *, entry: str, state: str
    ) -> int:
        """Append one character's record of one scene. `history` starts NULL;
        a rollup completes the row later. One row per (scene, character) —
        a second write for the same pair is a bug and fails loudly."""
        now = self._db.now()
        with self._db.conn as conn:
            # fmt: off
            cur = conn.execute(
                "INSERT INTO journals (story_id, scene_id, character_id, entry, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (story_id, scene_id, character_id, self._db.seal(entry), self._db.seal(state), now, now),
            )
            # fmt: on
        return int(cur.lastrowid or 0)

    def list(self, story_id: int) -> builtins.list[Journal]:
        """Every journal row of the story, oldest first."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT id, scene_id, character_id, entry, state, history FROM journals WHERE story_id = ? ORDER BY id",
            (story_id,),
        ).fetchall()
        # fmt: on
        return [
            Journal(
                id=int(jid),
                scene_id=int(sid),
                character_id=int(cid),
                entry=self._db.unseal(entry),
                state=self._db.unseal(state),
                history=self._db.unseal(history),
            )
            for jid, sid, cid, entry, state, history in rows
        ]

    # ---------- getters and setters ----------

    def get_current(self, story_id: int) -> dict[int, CharacterMemory]:
        """Each character's memory as of now, keyed by character id."""
        # fmt: off
        states = {
            int(cid): self._db.unseal(sealed)
            for cid, sealed in self._db.conn.execute(
                "SELECT character_id, state FROM journals WHERE id IN (SELECT MAX(id) FROM journals WHERE story_id = ? GROUP BY character_id)",
                (story_id,),
            )
        }
        # fmt: on
        if not states:
            return {}
        # fmt: off
        histories = {
            int(cid): self._db.unseal(sealed)
            for cid, sealed in self._db.conn.execute(
                "SELECT character_id, history FROM journals WHERE id IN (SELECT MAX(id) FROM journals WHERE story_id = ? AND history IS NOT NULL GROUP BY character_id)",
                (story_id,),
            )
        }
        entries: dict[int, list[str]] = {}
        for cid, sealed in self._db.conn.execute(
            f"SELECT j.character_id, j.entry FROM journals j WHERE {_UNCOVERED} ORDER BY j.id",
            (story_id,),
        ):
            entries.setdefault(int(cid), []).append(self._db.unseal(sealed))
        # fmt: on
        return {
            cid: CharacterMemory(
                history=histories.get(cid, ""),
                entries=tuple(entry for entry in entries.get(cid, ()) if entry),
                state=state,
            )
            for cid, state in states.items()
        }

    def get_by_scene(self, scene_id: int) -> builtins.list[Journal]:
        """Every journal row written at one scene's close."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT id, scene_id, character_id, entry, state, history FROM journals WHERE scene_id = ? ORDER BY id",
            (scene_id,),
        ).fetchall()
        # fmt: on
        return [
            Journal(
                id=int(jid),
                scene_id=int(sid),
                character_id=int(cid),
                entry=self._db.unseal(entry),
                state=self._db.unseal(state),
                history=self._db.unseal(history),
            )
            for jid, sid, cid, entry, state, history in rows
        ]

    def get_rollups_due(self, story_id: int) -> builtins.list[tuple[int, int]]:
        """(character id, journal row id) pairs the next history rollups
        belong on: each character whose NEWEST journal row lacks a history —
        freshly written at a scene close, or invalidated by an entry edit.
        A character absent from the latest scenes has a rollup on their
        newest row already and is not due. Id-only; nothing is decrypted."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT character_id, id FROM journals WHERE story_id = ? AND history IS NULL AND id IN ("
            "    SELECT MAX(id) FROM journals WHERE story_id = ? GROUP BY character_id) "
            "ORDER BY character_id",
            (story_id, story_id),
        ).fetchall()
        # fmt: on
        return [(int(cid), int(jid)) for cid, jid in rows]

    def get_entries(self, story_id: int, character_id: int) -> builtins.list[str]:
        """Every entry this character has written, oldest first — the whole
        input to a history rollup."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT entry FROM journals WHERE story_id = ? AND character_id = ? ORDER BY id",
            (story_id, character_id),
        ).fetchall()
        # fmt: on
        return [text for (sealed,) in rows if (text := self._db.unseal(sealed))]

    def set_history(self, journal_id: int, history: str) -> None:
        """Attach a freshly composed rollup to the row it was generated at."""
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE journals SET history = ?, updated_at = ? WHERE id = ?",
                (self._db.seal(history), self._db.now(), journal_id),
            )
            # fmt: on

    def set_entry(self, journal_id: int, entry: str) -> None:
        """The author's correction of one entry. Entries are rollup inputs,
        so every history composed from the old text — this row's or a later
        one's — is nulled; the next pass rebuilds from the corrected record."""
        row = self._db.conn.execute(
            "SELECT story_id, character_id FROM journals WHERE id = ?", (journal_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no journal row {journal_id}")
        story_id, character_id = int(row[0]), int(row[1])
        now = self._db.now()
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE journals SET entry = ?, updated_at = ? WHERE id = ?",
                (self._db.seal(entry), now, journal_id),
            )
            conn.execute(
                "UPDATE journals SET history = NULL, updated_at = ? WHERE story_id = ? AND character_id = ? AND id >= ?",
                (now, story_id, character_id, journal_id),
            )
            # fmt: on

    def set_state(self, journal_id: int, state: str) -> None:
        """The author's correction of a state snapshot — the character's
        latest row only. Older states are superseded fossils; an edit there
        would change nothing, so it is refused rather than absorbed."""
        row = self._db.conn.execute(
            "SELECT story_id, character_id FROM journals WHERE id = ?", (journal_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no journal row {journal_id}")
        # fmt: off
        latest = self._db.conn.execute(
            "SELECT MAX(id) FROM journals WHERE story_id = ? AND character_id = ?",
            (int(row[0]), int(row[1])),
        ).fetchone()[0]
        # fmt: on
        if latest != journal_id:
            raise ValueError("only the latest journal row's state can be edited")
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE journals SET state = ?, updated_at = ? WHERE id = ?",
                (self._db.seal(state), self._db.now(), journal_id),
            )
            # fmt: on
