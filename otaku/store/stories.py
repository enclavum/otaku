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
import re
from collections.abc import Container
from dataclasses import dataclass
from datetime import datetime

from otaku.store.database import Database
from otaku.store.schema import Message, Story

# The numbered suffix a fork's title carries — every one it has collected,
# so numbering works off the stem and the suffixes can never pile up
# ("The River - 3 - 3 - 2"). The group captures the last repetition, which
# is the number the title actually reads as.
_FORK_SUFFIX = re.compile(r"(?: - (\d+))+$")


@dataclass(frozen=True)
class StoryListing:
    """One row of the story browser. `title`, `story_so_far`, and
    `first_user` are the row's label fallbacks, in that order; each is ""
    when absent."""

    id: int
    title: str
    story_so_far: str  # the newest history rollup among current scenes
    first_user: str  # the first user message of the current chain
    model: str  # "provider/model" behind the newest reply
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
        """Every story, most recently played first. One flat read of the
        message trees serves every chain walk; per story, only the label
        fields decrypt — one rollup, one first message, the title."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT id, title, updated_at, head_id FROM stories",
        ).fetchall()
        # fmt: on
        tree = self._get_tree_rows([int(r[0]) for r in rows if r[3] is not None])

        chains: dict[int, set[int]] = {}
        lengths: dict[int, int] = {}
        first_user_ids: dict[int, int] = {}
        models: dict[int, str] = {}
        for story_row in rows:
            story_id = int(story_row[0])
            chain = chains[story_id] = set()
            node = story_row[3]
            while node is not None and node in tree:  # head → root; finite via the parent CHECK
                chain.add(node)
                parent, role, provider, model = tree[node]
                if role == "user":
                    first_user_ids[story_id] = node  # last user seen = root-most
                if role == "assistant" and model and story_id not in models:
                    models[story_id] = f"{provider}/{model}" if provider else model
                node = parent
            lengths[story_id] = len(chain)

        first_users = self._get_bodies(first_user_ids)
        so_far = self._get_stories_so_far(chains)
        listings = [
            StoryListing(
                id=int(story_id),
                title=self._db.unseal(title),
                story_so_far=so_far.get(int(story_id), ""),
                first_user=first_users.get(int(story_id), ""),
                model=models.get(int(story_id), ""),
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

    def get_texts(self) -> dict[int, str]:
        """Each story's full current-chain text, lowercased — the browser's
        content-filter index, built lazily on the first search keystroke
        (it decrypts every story)."""
        ids = [int(row[0]) for row in self._db.conn.execute("SELECT id FROM stories")]
        return {
            story_id: " ".join(m.body for m in self.get_messages(story_id)).lower()
            for story_id in ids
        }

    def fork(
        self,
        story_id: int,
        *,
        from_message_id: int | None = None,
        title: str | None = None,
    ) -> int:
        """A new story branched off at `from_message_id` (default: the head).
        An explicit `title` is used verbatim; otherwise the source's title
        gains a number ("<title> - N") — one that already carries a number
        is renumbered, never suffixed again — and an untitled source forks
        untitled. Deep copy through the cut: the message chain, the cast,
        every scene of that chain, and their journals — a branch is the
        story so far, memory included. Extraction's settle margin has no
        business here: it exists because /undo and /regen can rewind the
        newest turns, and these scenes already cleared it in the source.
        A scene the copy later undoes past simply stops being current,
        exactly as in the story it came from. Returns the new story id."""
        source = self.get(story_id)
        if source is None:
            raise ValueError(f"no story {story_id}")
        if from_message_id is not None:
            # fmt: off
            owner = self._db.conn.execute(
                "SELECT story_id FROM messages WHERE id = ?",
                (from_message_id,),
            ).fetchone()
            # fmt: on
            if owner is None or int(owner[0]) != story_id:
                raise ValueError(f"message {from_message_id} is not in story {story_id}")
        cut = from_message_id if from_message_id is not None else source.head_id
        chain = self._get_chain_ids(cut)
        copied = set(chain)
        if title is None:
            title = self._fork_title(source.title)
        now = self._db.now()

        with self._db.conn as conn:
            # fmt: off
            cur = conn.execute(
                "INSERT INTO stories (forked_from_id, title, system, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (story_id, self._db.seal_opt(title or None), self._db.seal_opt(source.system or None), now, now),
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

    def _fork_title(self, base: str) -> str | None:
        """`fork_title` over the titles already in the database. Titles
        only — the full listing would decrypt every story's labels just to
        number one fork."""
        # fmt: off
        rows = self._db.conn.execute("SELECT title FROM stories").fetchall()
        # fmt: on
        return fork_title(base, {self._db.unseal(title) for (title,) in rows})

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

    def _get_tree_rows(
        self, story_ids: builtins.list[int]
    ) -> dict[int, tuple[int | None, str, str | None, str | None]]:
        """id → (parent_id, role, provider, model) for every message of these
        stories — the flat, nothing-decrypted read behind the chain walks."""
        if not story_ids:
            return {}
        placeholders = ",".join("?" * len(story_ids))
        # fmt: off
        rows = self._db.conn.execute(
            f"SELECT id, parent_id, role, provider, model FROM messages WHERE story_id IN ({placeholders})",
            tuple(story_ids),
        ).fetchall()
        # fmt: on
        return {
            int(mid): (parent, str(role), provider, model)
            for mid, parent, role, provider, model in rows
        }

    def _get_bodies(self, wanted: dict[int, int]) -> dict[int, str]:
        """story id → decrypted body, for one chosen message per story."""
        if not wanted:
            return {}
        by_message = {mid: story_id for story_id, mid in wanted.items()}
        placeholders = ",".join("?" * len(by_message))
        # fmt: off
        rows = self._db.conn.execute(
            f"SELECT id, body FROM messages WHERE id IN ({placeholders})",
            tuple(by_message),
        ).fetchall()
        # fmt: on
        return {by_message[int(mid)]: self._db.unseal(body) for mid, body in rows}

    def _get_stories_so_far(self, chains: dict[int, set[int]]) -> dict[int, str]:
        """story id → its newest story-so-far rollup among current scenes
        (the `get_story_so_far` rule, across all stories, decrypting one
        per story)."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT story_id, end_message_id, history FROM scenes WHERE history IS NOT NULL ORDER BY id",
        ).fetchall()
        # fmt: on
        newest: dict[int, bytes] = {}
        for story_id, end, history in rows:  # id order — the last write wins
            if end in chains.get(int(story_id), ()):
                newest[int(story_id)] = history
        return {story_id: self._db.unseal(sealed) for story_id, sealed in newest.items()}


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


def fork_title(base: str, taken: Container[str]) -> str | None:
    """The title a fork of `base` gets: the first name not in `taken` of
    the form "<stem> - N", counting up FROM `base`'s own number.

    A numbered title is renumbered off its stem — never suffixed again
    ("The River - 3" forks to "- 4", never "- 3 - 2") and never below
    itself ("The River - 5" forks to "- 6" even while "- 4" stands free).
    An unnumbered title starts at 2, only a trailing " - <digits>" counts
    as numbering ("Chapter 7" forks to "Chapter 7 - 2"), and an untitled
    source forks untitled: "" in, None out.
    """
    if not base:
        return None
    numbered = _FORK_SUFFIX.search(base)
    stem = base[: numbered.start()] if numbered else base
    n = int(numbered.group(1)) + 1 if numbered else 2
    if not stem:  # a title that is nothing but a number keeps itself
        stem, n = base, 2
    while f"{stem} - {n}" in taken:
        n += 1
    return f"{stem} - {n}"
