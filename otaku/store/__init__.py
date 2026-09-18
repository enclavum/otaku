"""The story store: SQLite (optionally encrypted) behind explicit operations,
and the files folder beside it for the bytes a row only names.

`Store.open` builds the database nucleus over the paths it is handed, the
files folder beside it under the same cipher, and exposes one ops group
per table:

    store.stories     stories and their message trees (source)
    store.messages    individual messages (source)
    store.scenes      scenes and the story-so-far rollup (derivatives)
    store.characters  the cast (derivatives)
    store.journals    per-character memory (derivatives)
    store.usage       token accounting
    store.history     the terminal's Up/Down input history
    store.files       the files folder beside the database: a turn's pictures (`FileStore`)
"""

from pathlib import Path
from typing import Self

from otaku.encryption import Cipher
from otaku.store.database import Database, DatabaseError, Note, is_encrypted
from otaku.store.files import FileStore
from otaku.store.ops.lore import CharacterOps, JournalOps, SceneOps
from otaku.store.ops.records import HistoryOps, UsageOps
from otaku.store.ops.stories import MessagesOps, StoryOps

__all__ = ["DatabaseError", "Note", "Store", "is_encrypted"]


class Store:
    def __init__(self, db: Database, files: FileStore) -> None:
        # Deleting a story spans both stores — the one operation that
        # does — so the stories ops are handed the folder by name.
        self.stories = StoryOps(db, files)
        self.messages = MessagesOps(db)
        self.scenes = SceneOps(db)
        self.characters = CharacterOps(db)
        self.journals = JournalOps(db)
        self.usage = UsageOps(db)
        self.history = HistoryOps(db)
        self.files = files
        self._db = db

    @classmethod
    def open(cls, db_path: Path, cipher: Cipher, *, backups_dir: Path, keep: int) -> Self:
        """`Database.open`'s contract (creation, migration, the canary,
        the daily backup), the files folder beside the database under
        the same cipher, wrapped with the ops groups."""
        db = Database.open(db_path, cipher, backups_dir=backups_dir, keep=keep)
        return cls(db, FileStore(db_path.parent / "files", cipher))

    @property
    def notes(self) -> list[Note]:
        """The nucleus's administrative facts (migration ran, backup
        written or failed) — the caller logs every one and says the ones
        carrying a `show` line; the store never does either."""
        return self._db.notes

    def close(self) -> None:
        self._db.close()
