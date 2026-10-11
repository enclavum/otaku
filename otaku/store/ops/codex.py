"""The codex: what every story may draw on and no story owns — one ops
class per table.

A card is a character as the reader keeps it across stories. A story's
character links to one (`characters.card_id`) and holds a copy of its
fields, so nothing written here reaches a story, and deleting a card
leaves its story characters unlinked (`ON DELETE SET NULL`). Its
pictures are files in the folder beside the database, named in a plain
column as a message's are (`schema.Attachment`).
"""

# Deferred annotations: the ops classes define `list` methods, which
# would otherwise shadow the builtin inside later annotations.
from __future__ import annotations

import builtins
import json
from collections.abc import Sequence
from typing import Any

from otaku.store.database import Database
from otaku.store.schema import Attachment, Card


class CardOps:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        name: str,
        *,
        aliases: Sequence[str] = (),
        tagline: str = "",
        fields: str = "",
        pictures: Sequence[Attachment] = (),
        digests: Sequence[str] = (),
    ) -> int:
        now = self._db.now()
        with self._db.conn as conn:
            # fmt: off
            cur = conn.execute(
                "INSERT INTO cards (name, aliases, tagline, fields, pictures, digests, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self._db.seal(name), self._seal_aliases(aliases), self._db.seal_opt(tagline or None), self._db.seal_opt(fields or None), Attachment.to_json(pictures), _digests_json(digests), now, now),
            )
            # fmt: on
        return int(cur.lastrowid or 0)

    def get(self, card_id: int) -> Card | None:
        # fmt: off
        row = self._db.conn.execute(
            "SELECT id, name, aliases, tagline, fields, pictures, digests, updated_at FROM cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        # fmt: on
        return self._card(row) if row is not None else None

    def list(self) -> builtins.list[Card]:
        """Every card, in the order they were made."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT id, name, aliases, tagline, fields, pictures, digests, updated_at FROM cards ORDER BY id",
        ).fetchall()
        # fmt: on
        return [self._card(row) for row in rows]

    def update(
        self,
        card_id: int,
        *,
        name: str | None = None,
        aliases: Sequence[str] | None = None,
        tagline: str | None = None,
        fields: str | None = None,
    ) -> None:
        """The reader's edit of a card. None leaves a field as it is; ""
        and () clear one — but the name, which a card cannot be without."""
        now = self._db.now()
        with self._db.conn as conn:
            # fmt: off
            if name is not None:
                conn.execute(
                    "UPDATE cards SET name = ?, updated_at = ? WHERE id = ?",
                    (self._db.seal(name), now, card_id),
                )
            if aliases is not None:
                conn.execute(
                    "UPDATE cards SET aliases = ?, updated_at = ? WHERE id = ?",
                    (self._seal_aliases(aliases), now, card_id),
                )
            if tagline is not None:
                conn.execute(
                    "UPDATE cards SET tagline = ?, updated_at = ? WHERE id = ?",
                    (self._db.seal_opt(tagline or None), now, card_id),
                )
            if fields is not None:
                conn.execute(
                    "UPDATE cards SET fields = ?, updated_at = ? WHERE id = ?",
                    (self._db.seal_opt(fields or None), now, card_id),
                )
            # fmt: on

    def delete(self, card_id: int) -> None:
        """Remove a card; the story characters linked to it stay, unlinked,
        and its pictures stay in the folder until `Store.sweep_files`."""
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "DELETE FROM cards WHERE id = ?", (card_id,)
            )
            # fmt: on

    # ---------- getters and setters ----------

    def get_file_names(self) -> set[str]:
        """Every file name some card's pictures carry."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT DISTINCT json_extract(p.value, '$.file') FROM cards c, json_each(c.pictures) p WHERE c.pictures IS NOT NULL",
        ).fetchall()
        # fmt: on
        return {str(row[0]) for row in rows if row[0] is not None}

    def set_pictures(self, card_id: int, pictures: Sequence[Attachment]) -> None:
        """A card's pictures, replaced — in their order, the first the
        portrait."""
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "UPDATE cards SET pictures = ?, updated_at = ? WHERE id = ?",
                (Attachment.to_json(pictures), self._db.now(), card_id),
            )
            # fmt: on

    # ---------- shaping ----------

    def _card(self, row: Any) -> Card:
        card_id, name, aliases, tagline, fields, pictures, digests, updated = row
        return Card(
            id=int(card_id),
            name=self._db.unseal(name),
            aliases=self._unseal_aliases(aliases),
            tagline=self._db.unseal(tagline),
            fields=self._db.unseal(fields),
            pictures=Attachment.from_json(pictures),
            digests=tuple(json.loads(digests)) if digests else (),
            updated_at=str(updated),
        )

    def _seal_aliases(self, aliases: Sequence[str]) -> bytes | None:
        return self._db.seal_opt(json.dumps(list(aliases)) if aliases else None)

    def _unseal_aliases(self, sealed: bytes | None) -> tuple[str, ...]:
        raw = self._db.unseal(sealed)
        if not raw:
            return ()
        try:
            return tuple(str(alias) for alias in json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return ()


def _digests_json(digests: Sequence[str]) -> str | None:
    """The column's text for these digests: a JSON list, or NULL for
    none — never an empty list pretending to be absent."""
    return json.dumps(list(digests)) if digests else None
