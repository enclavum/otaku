"""Bookkeeping records: token accounting and the REPL's input history.

Neither is story content. `token_usage` holds numbers and labels only, one
row per completed model request, kept on story deletion. `history` is the
terminal's Up/Down line history — shell-style, global on purpose, capped.
"""

import builtins
from dataclasses import dataclass

from otaku.store.database import Database

_HISTORY_LIMIT = 20


@dataclass(frozen=True)
class UsageTotal:
    """One (purpose, provider, model) group of the token_usage table."""

    purpose: str
    provider: str
    model: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
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
        duration_seconds: float | None = None,
    ) -> None:
        """`purpose` says what the tokens were spent on ('chat', 'lore', …).
        The duration is rounded here, at the one writer, so the column never
        carries float noise finer than anything this measures."""
        seconds = None if duration_seconds is None else round(duration_seconds, 1)
        with self._db.conn as conn:
            # fmt: off
            conn.execute(
                "INSERT INTO token_usage (story_id, provider, model, purpose, prompt_tokens, completion_tokens, duration_seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (story_id, provider, model, purpose, prompt_tokens, completion_tokens, seconds, self._db.now()),
            )
            # fmt: on

    def get_totals(self, story_id: int | None = None) -> builtins.list[UsageTotal]:
        """Accounting grouped by purpose, provider, and model — the whole
        database, or one story when given — sorted the same way."""
        where = "WHERE story_id = ?" if story_id is not None else ""
        params = (story_id,) if story_id is not None else ()
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT purpose, provider, model, COUNT(*),"
            "    COALESCE(SUM(prompt_tokens), 0),"
            "    COALESCE(SUM(completion_tokens), 0),"
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
                seconds=round(float(seconds), 1),
            )
            for purpose, provider, model, requests, prompt, completion, seconds in rows
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

    def get_recent(self, limit: int = _HISTORY_LIMIT) -> builtins.list[str]:
        """Up to `limit` submitted lines, most recent first."""
        # fmt: off
        rows = self._db.conn.execute(
            "SELECT body FROM history ORDER BY id DESC LIMIT ?",
            (max(0, limit),),
        ).fetchall()
        # fmt: on
        return [text for (body,) in rows if (text := self._db.unseal(body))]
