"""The tables that belong to no story: token accounting, the terminal's
input history, and the settings stories share.

`token_usage` holds numbers and labels only, one row per completed
model request, kept on story deletion. `history` is the terminal's
Up/Down line history — shell-style, global on purpose, capped; the one
store surface that belongs to a single frontend, exposed through
backend all the same. `settings` holds what stories SHARE, sealed key by
key. A setting is this table's when it is a text that may need sealing
(a config file is never sealed), and a config file's otherwise.
"""

# Deferred annotations: `list` appears in annotations near methods that
# shadow nothing here, kept for symmetry with the sibling ops modules.
from __future__ import annotations

from dataclasses import dataclass

from otaku.store.database import Database

_HISTORY_LIMIT = 20  # the one retention number both history methods share
# The `settings` row the shared reminder's text is kept under.
_SHARED_REMINDER_KEY = "shared_reminder"


@dataclass(frozen=True)
class UsageTotal:
    """One (purpose, provider, model) group of the token_usage table."""

    purpose: str
    provider: str
    model: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int  # of prompt_tokens, served from the provider's cache
    seconds: float


class UsageOps:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(
        self,
        provider: str,
        model: str,
        purpose: str,
        *,
        story_id: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cached_tokens: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """`purpose` says what the tokens were spent on ('chat', 'lore', …).
        The duration is rounded here, at the one writer, so the column never
        carries float noise finer than anything this measures."""
        seconds = None if duration_seconds is None else round(duration_seconds, 1)
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "INSERT INTO token_usage (story_id, provider, model, purpose, prompt_tokens, completion_tokens, cached_tokens, duration_seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (story_id, provider, model, purpose, prompt_tokens, completion_tokens, cached_tokens, seconds, self._db.now()),
            )
            # fmt: on

    def get_totals(self, story_id: int | None = None) -> list[UsageTotal]:
        """Accounting grouped by purpose, provider, and model — the whole
        database, or one story when given — sorted the same way."""
        where = "WHERE story_id = ?" if story_id is not None else ""
        params = (story_id,) if story_id is not None else ()
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT purpose, provider, model, COUNT(*),"
            "    COALESCE(SUM(prompt_tokens), 0),"
            "    COALESCE(SUM(completion_tokens), 0),"
            "    COALESCE(SUM(cached_tokens), 0),"
            "    COALESCE(SUM(duration_seconds), 0.0) "
            f"FROM token_usage {where} "
            "GROUP BY purpose, provider, model "
            "ORDER BY purpose, provider, model",
            params,
        ).fetchall()
        # fmt: on
        return [
            UsageTotal(
                purpose=str(purpose),
                provider=str(provider),
                model=str(model),
                requests=int(requests),
                prompt_tokens=int(prompt),
                completion_tokens=int(completion),
                cached_tokens=int(cached),
                seconds=round(float(seconds), 1),
            )
            for purpose, provider, model, requests, prompt, completion, cached, seconds in rows
        ]


class HistoryOps:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, text: str, *, limit: int = _HISTORY_LIMIT) -> None:
        """Record one submitted line, then prune to the newest `limit`.
        Blank lines and an immediate repeat of the last entry are skipped."""
        text = text.strip("\n")
        if not text.strip():
            return
        last = self._db.conn.execute("SELECT body FROM history ORDER BY id DESC LIMIT 1").fetchone()
        if last is not None and self._db.unseal(last[0]) == text:
            return
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "INSERT INTO history (body, created_at) VALUES (?, ?)",
                (self._db.seal(text), self._db.now()),
            )
            conn.execute(
                "DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT ?)",
                (max(0, limit),),
            )
            # fmt: on

    def get_recent(self, limit: int = _HISTORY_LIMIT) -> list[str]:
        """Up to `limit` submitted lines, most recent first."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT body FROM history ORDER BY id DESC LIMIT ?",
            (max(0, limit),),
        ).fetchall()
        # fmt: on
        return [text for (body,) in rows if (text := self._db.unseal(body))]


class SettingsOps:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> str:
        """The value under `key`; "" when there is none."""
        row = self._db.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return self._db.unseal(row[0]) if row else ""

    def set(self, key: str, value: str) -> None:
        """Write the value under `key`. An emptied value DELETES its row:
        nothing sealed and empty pretends to be absent."""
        now = self._db.now()
        with self._db.conn as conn:
            # fmt: off
            if value:
                conn.execute(
                    "INSERT INTO settings (key, value, created_at, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (key, self._db.seal(value), now, now),
                )
            else:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            # fmt: on

    def get_shared_reminder(self) -> str:
        """The reminder stories share — one text, which every story that
        switched it on is sent; "" when none."""
        return self.get(_SHARED_REMINDER_KEY)

    def set_shared_reminder(self, text: str) -> None:
        """The shared reminder's text; "" clears it."""
        self.set(_SHARED_REMINDER_KEY, text)
