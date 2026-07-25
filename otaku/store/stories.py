"""The source aggregate: stories and their message trees.

Message rows form a sibling tree via `parent_id`; the story's `head_id` is
the current position, and its messages — root to head — are what a session
sees. The API is explicit operations: `append` adds at the head, undo is
`set_head` (abandoned turns stay in the tree as siblings), regenerate is a
`set_head` + `append` that diverges into a sibling.

`fork` deep-copies a story through the cut — messages, surviving scenes,
their journals, and the cast, ids remapped — so every story is fully
self-contained.
"""

import builtins
from dataclasses import dataclass
from datetime import datetime

from otaku.store.database import Database
from otaku.store.schema import Message, Story


@dataclass(frozen=True)
class StoryListing:
    """One row of the story browser: most recently played first."""

    id: int
    title: str
    updated_at: datetime
    num_messages: int  # length of the current chain, not the tree


class StoryOps:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, title: str | None = None) -> int:
        now = self._db.now()
        with self._db.conn as conn:
            # fmt: off
            cur = conn.execute(
                "INSERT INTO stories (title, created_at, updated_at) VALUES (?, ?, ?)",
                (self._db.seal_opt(title), now, now),
            )
            # fmt: on
        return int(cur.lastrowid or 0)

    def get(self, story_id: int) -> Story | None:
        # fmt: off
        row = self._db.conn.execute(
            "SELECT id, title, system, head_id, forked_from_id FROM stories WHERE id = ?",
            (story_id,),
        ).fetchone()
        # fmt: on
        if row is None:
            return None
        return Story(
            id=int(row[0]),
            title=self._db.unseal(row[1]),
            system=self._db.unseal(row[2]),
            head_id=row[3],
            forked_from_id=row[4],
        )

    def list(self) -> builtins.list[StoryListing]:
        """Every story, most recently played first."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT id, title, updated_at, head_id FROM stories",
        ).fetchall()
        # fmt: on
        lengths = self._get_path_lengths({int(r[0]): r[3] for r in rows})
        listings = [
            StoryListing(
                id=int(story_id),
                title=self._db.unseal(title),
                updated_at=datetime.fromisoformat(updated_at),
                num_messages=lengths[int(story_id)],
            )
            for story_id, title, updated_at, _head in rows
        ]
        # Ties (same second) break toward the newer story, so the order is
        # deterministic however fast the stories were touched.
        return sorted(listings, key=lambda item: (item.updated_at, item.id), reverse=True)

    def rename(self, story_id: int, title: str) -> None:
        """Deliberately does not bump updated_at: titling is metadata and
        must not reorder the story list."""
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE stories SET title = ? WHERE id = ?",
                (self._db.seal(title), story_id),
            )
            # fmt: on

    def delete(self, story_id: int) -> None:
        """The one destructive act, and it is the user's: drop a story and
        everything it owns. The story row references its own head message,
        so FK checks defer to commit."""
        self._db.conn.execute("PRAGMA defer_foreign_keys = ON")
        with self._db.conn as conn:
            conn.execute("DELETE FROM stories WHERE id = ?", (story_id,))

    # ---------- getters and setters ----------

    def exists(self, story_id: int) -> bool:
        row = self._db.conn.execute("SELECT 1 FROM stories WHERE id = ?", (story_id,)).fetchone()
        return row is not None

    def get_system(self, story_id: int) -> str:
        row = self._db.conn.execute(
            "SELECT system FROM stories WHERE id = ?", (story_id,)
        ).fetchone()
        return self._db.unseal(row[0]) if row else ""

    def set_system(self, story_id: int, text: str) -> None:
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE stories SET system = ? WHERE id = ?",
                (self._db.seal(text), story_id),
            )
            # fmt: on

    def get_head(self, story_id: int) -> int | None:
        row = self._db.conn.execute(
            "SELECT head_id FROM stories WHERE id = ?", (story_id,)
        ).fetchone()
        return row[0] if row else None

    def set_head(self, story_id: int, message_id: int | None) -> None:
        """Undo's primitive: point the story at an earlier message (or None
        for an empty story). The turns after it stay in the tree as
        siblings — nothing is deleted."""
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE stories SET head_id = ?, updated_at = ? WHERE id = ?",
                (message_id, self._db.now(), story_id),
            )
            # fmt: on

    # ---------- the message tree ----------

    def append(self, story_id: int, message: Message) -> int:
        """Add one turn at the head and advance it. `message.id` is ignored;
        the assigned id is returned."""
        now = self._db.now()
        with self._db.conn as conn:
            head = self.get_head(story_id)
            # fmt: off
            cur = conn.execute(
                "INSERT INTO messages (story_id, parent_id, role, kind, speaker_id, speaker, body, framing, provider, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (story_id, head, message.role, message.kind, message.speaker_id, self._db.seal_opt(message.speaker), self._db.seal(message.body), self._db.seal_opt(message.framing), message.provider, message.model, now, now),
            )
            message_id = int(cur.lastrowid or 0)
            conn.execute(
                "UPDATE stories SET head_id = ?, updated_at = ? WHERE id = ?",
                (message_id, now, story_id),
            )
            # fmt: on
        return message_id

    def get_messages(self, story_id: int) -> builtins.list[Message]:
        """The story as a session sees it: its messages, decrypted,
        root → head."""
        head = self.get_head(story_id)
        if head is None:
            return []
        # fmt: off
        rows = self._db.conn.execute(
            "WITH RECURSIVE chain(id, parent_id, depth) AS ("
            "    SELECT id, parent_id, 0 FROM messages WHERE id = ?"
            "    UNION ALL"
            "    SELECT m.id, m.parent_id, chain.depth + 1 FROM messages m JOIN chain ON m.id = chain.parent_id) "
            "SELECT m.id, m.role, m.kind, m.speaker_id, m.speaker, m.body, m.framing, m.provider, m.model "
            "FROM chain JOIN messages m ON m.id = chain.id ORDER BY chain.depth DESC",
            (head,),
        ).fetchall()
        # fmt: on
        return [
            Message(
                id=int(mid),
                role=str(role),
                kind=str(kind),
                speaker_id=speaker_id,
                speaker=self._db.unseal_opt(speaker),
                body=self._db.unseal(body),
                framing=self._db.unseal_opt(framing),
                provider=provider,
                model=model,
            )
            for mid, role, kind, speaker_id, speaker, body, framing, provider, model in rows
        ]

    def get_messages_ids(self, story_id: int) -> builtins.list[int]:
        """Ids of the story's messages, root → head. Id-only — nothing is
        decrypted, so callers that need boundaries pay nothing."""
        return self._get_chain_ids(self.get_head(story_id))

    def fork(self, story_id: int, *, from_message_id: int | None = None) -> int:
        """A new story branched off at `from_message_id` (default: the head),
        titled "<source title> - N". Deep copy through the cut: the message
        chain, the cast, the scenes that lie fully inside it (a scene cut
        mid-span is not copied — its span becomes unextracted tail), and
        those scenes' journals. Returns the new story id."""
        source = self.get(story_id)
        if source is None:
            raise ValueError(f"no story {story_id}")
        cut = from_message_id if from_message_id is not None else source.head_id
        chain = self._get_chain_ids(cut)
        copied = set(chain)
        title = self._fork_title(source.title)
        now = self._db.now()

        with self._db.conn as conn:
            # fmt: off
            cur = conn.execute(
                "INSERT INTO stories (forked_from_id, title, system, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (story_id, self._db.seal(title), self._db.seal_opt(source.system or None), now, now),
            )
            new_story = int(cur.lastrowid or 0)

            character_map: dict[int, int] = {}
            rows = conn.execute(
                "SELECT id, name, aliases, description FROM characters WHERE story_id = ?",
                (story_id,),
            ).fetchall()
            for cid, name, aliases, description in rows:
                cur = conn.execute(
                    "INSERT INTO characters (story_id, name, aliases, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (new_story, name, aliases, description, now, now),
                )
                character_map[int(cid)] = int(cur.lastrowid or 0)

            message_map: dict[int, int] = {}
            for mid in chain:
                row = conn.execute(
                    "SELECT parent_id, role, kind, speaker_id, speaker, body, framing, provider, model, created_at FROM messages WHERE id = ?",
                    (mid,),
                ).fetchone()
                parent, role, kind, speaker_id, speaker = row[:5]
                body, framing, provider, model, created = row[5:]
                cur = conn.execute(
                    "INSERT INTO messages (story_id, parent_id, role, kind, speaker_id, speaker, body, framing, provider, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_story, message_map.get(parent), role, kind, character_map.get(speaker_id), speaker, body, framing, provider, model, created, now),
                )
                message_map[int(mid)] = int(cur.lastrowid or 0)

            scene_map: dict[int, int] = {}
            rows = conn.execute(
                "SELECT id, start_message_id, end_message_id, title, summary, history, created_at FROM scenes WHERE story_id = ? ORDER BY id",
                (story_id,),
            ).fetchall()
            for sid, start, end, s_title, summary, history, created in rows:
                if start not in copied or end not in copied:
                    continue
                cur = conn.execute(
                    "INSERT INTO scenes (story_id, start_message_id, end_message_id, title, summary, history, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_story, message_map[start], message_map[end], s_title, summary, history, created, now),
                )
                scene_map[int(sid)] = int(cur.lastrowid or 0)

            rows = conn.execute(
                "SELECT scene_id, character_id, entry, state, history, created_at FROM journals WHERE story_id = ? ORDER BY id",
                (story_id,),
            ).fetchall()
            for scene_id, character_id, entry, state, history, created in rows:
                if scene_id not in scene_map:
                    continue
                conn.execute(
                    "INSERT INTO journals (story_id, scene_id, character_id, entry, state, history, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_story, scene_map[scene_id], character_map[character_id], entry, state, history, created, now),
                )

            new_head = message_map.get(cut) if cut is not None else None
            conn.execute(
                "UPDATE stories SET head_id = ? WHERE id = ?",
                (new_head, new_story),
            )
            # fmt: on
        return new_story

    # ---------- internals ----------

    def _fork_title(self, base: str) -> str:
        """The first free "<base> - N", N from 2 — the user renames at will."""
        base = base or "story"
        taken = {item.title for item in self.list()}
        n = 2
        while f"{base} - {n}" in taken:
            n += 1
        return f"{base} - {n}"

    def _get_chain_ids(self, head_id: int | None) -> builtins.list[int]:
        """Walk the parent chain from a head; ids come back root → head."""
        if head_id is None:
            return []
        # fmt: off
        rows = self._db.conn.execute(
            "WITH RECURSIVE chain(id, parent_id, depth) AS ("
            "    SELECT id, parent_id, 0 FROM messages WHERE id = ?"
            "    UNION ALL"
            "    SELECT m.id, m.parent_id, chain.depth + 1 FROM messages m JOIN chain ON m.id = chain.parent_id) "
            "SELECT id FROM chain ORDER BY depth DESC",
            (head_id,),
        ).fetchall()
        # fmt: on
        return [int(row[0]) for row in rows]

    def _get_path_lengths(self, heads: dict[int, int | None]) -> dict[int, int]:
        """Message count per story by walking parent links in one flat read —
        the tree's `parent_id < id` CHECK makes the walk finite."""
        live = [story_id for story_id, head in heads.items() if head is not None]
        if not live:
            return dict.fromkeys(heads, 0)
        placeholders = ",".join("?" * len(live))
        # fmt: off
        parents: dict[int, int | None] = dict(
            self._db.conn.execute(
                f"SELECT id, parent_id FROM messages WHERE story_id IN ({placeholders})",
                tuple(live),
            )
        )
        # fmt: on
        lengths: dict[int, int] = {}
        for story_id, head in heads.items():
            node, count = head, 0
            while node is not None:
                count += 1
                node = parents.get(node)
            lengths[story_id] = count
        return lengths


class MessagesOps:
    """Operations on individual messages, independent of any story's head."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def update(self, message_id: int, body: str) -> None:
        """The author's correction of one message's text — the one deliberate
        rewrite of source prose. Derivatives built from the old text stay;
        they are corrected in the lore browser."""
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE messages SET body = ?, updated_at = ? WHERE id = ?",
                (self._db.seal(body), self._db.now(), message_id),
            )
            # fmt: on

    def set_speaker(self, message_id: int, character_id: int | None, name: str) -> None:
        """Attribute a line to a cast member. Fill-only: an existing
        attribution is never overwritten — the extraction labels lines, it
        does not override facts."""
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE messages SET speaker_id = ?, speaker = ?, updated_at = ? WHERE id = ? AND speaker IS NULL",
                (character_id, self._db.seal(name), self._db.now(), message_id),
            )
            # fmt: on

    def get_parent(self, message_id: int) -> int | None:
        row = self._db.conn.execute(
            "SELECT parent_id FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return row[0] if row else None

    def count_body_chars(self, message_ids: list[int]) -> int:
        """Total characters of these messages' bodies — what the scene gate
        measures. Decrypts only the rows asked for; it has to decrypt, since
        a sealed blob's byte length is a bad proxy for character count."""
        if not message_ids:
            return 0
        placeholders = ",".join("?" * len(message_ids))
        # fmt: off
        rows = self._db.conn.execute(
            f"SELECT body FROM messages WHERE id IN ({placeholders})",
            tuple(message_ids),
        ).fetchall()
        # fmt: on
        return sum(len(self._db.unseal(row[0])) for row in rows)
