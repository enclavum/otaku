"""The story store: SQLite (optionally encrypted) behind explicit operations.

`Store.open` builds the database nucleus and exposes one ops group per
table:

    store.stories     stories and their message trees (source)
    store.messages    individual messages (source)
    store.scenes      scenes and the story-so-far rollup (derivatives)
    store.characters  the cast (derivatives)
    store.journals    per-character memory (derivatives)
    store.usage       token accounting
    store.history     the REPL's Up/Down input history
"""

from typing import Self

from otaku.crypto import Cipher
from otaku.paths import Paths
from otaku.store.database import Database, DatabaseError, is_encrypted
from otaku.store.lore import CharacterOps, JournalOps, SceneOps
from otaku.store.records import HistoryOps, UsageOps
from otaku.store.stories import MessagesOps, StoryOps

__all__ = ["DatabaseError", "Store", "is_encrypted"]


class Store:
    def __init__(self, db: Database) -> None:
        self._db = db
        self.stories = StoryOps(db)
        self.messages = MessagesOps(db)
        self.scenes = SceneOps(db)
        self.characters = CharacterOps(db)
        self.journals = JournalOps(db)
        self.usage = UsageOps(db)
        self.history = HistoryOps(db)

    @classmethod
    def open(cls, paths: Paths, cipher: Cipher, *, backups: int) -> Self:
        return cls(Database.open(paths, cipher, backups=backups))

    def close(self) -> None:
        self._db.close()
