"""The read-only reports both frontends show on the installation: the
tokens spent (`UsageResource`), what otaku knows of the model and the
run (`InfoResource`), and what each cloud account has left
(`BalanceResource`); a story's next request is the story's
(`api.story.context`).

What a resource answers is the report: the facts it is made of, and
`text()` — the same facts as a terminal pages them. A frontend that
PRINTS asks for the text; one that DRAWS (a table, a definition list,
the window diagram) takes the facts and draws them. Neither invents a
fact, neither rewords a sentence, and the two cannot drift because the
text is rendered from the facts beside it. The facts are gathered by the
domain (`app.reports.services`).
"""

from __future__ import annotations

from otaku.backend.app.reports import services as domain_reports
from otaku.backend.app.reports.schemas import (
    USAGE_SCOPES,
    Balance,
    BalanceReport,
    InfoReport,
    InfoSection,
    UsageReport,
    UsageRow,
)
from otaku.backend.base import Resource
from otaku.backend.errors import NO_MODEL_HINT


class UsageResource(Resource):
    """/usage — the tokens spent."""

    def get(self, *, story_id: int | None = None) -> UsageReport:
        """Tokens spent on a story — on every story where none is named.
        Grouped by what the tokens were spent on (chat, lore, …), then by
        provider and model. Raises NotFound when there is nothing to
        report."""
        rows = tuple(
            UsageRow(
                purpose=row.purpose,
                provider=row.provider,
                model=row.model,
                requests=row.requests,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                cached_tokens=row.cached_tokens,
                rate=row.completion_tokens / row.seconds if row.seconds > 0 else 0.0,
            )
            for row in domain_reports.get_usage(self._app, story_id)
        )
        return UsageReport(
            scope=dict(USAGE_SCOPES)["all" if story_id is None else ""],
            rows=rows,
            requests=sum(row.requests for row in rows),
            prompt_tokens=sum(row.prompt_tokens for row in rows),
            completion_tokens=sum(row.completion_tokens for row in rows),
            cached_tokens=sum(row.cached_tokens for row in rows),
        )


class InfoResource(Resource):
    """/info — what otaku knows of the model and the run."""

    def get(self, *, story_id: int | None = None) -> InfoReport:
        """Everything otaku knows about the active model and this run, in
        blocks, best-effort: network-backed fields are silently skipped.
        Without a model only that block is missing."""
        state, model, rows = domain_reports.get_info(self._app, story_id)
        return InfoReport(
            (
                InfoSection(rows=state),
                InfoSection(rows=model) if model is not None else InfoSection(note=NO_MODEL_HINT),
                InfoSection(rows=rows),
            )
        )


class BalanceResource(Resource):
    """/balance — what each cloud account has left."""

    def get(self, *, probe: bool = True) -> BalanceReport:
        """Every provider with an account to bill, and what each says it
        has left — EVERY one, a row with a dash where there is no figure,
        since that row is usually the one the reader came to look at.
        `probe=False` asks no network at all and returns the ROSTER —
        each keyed row with no figure and an EMPTY note, meaning "not
        asked yet": the page paints the whole slip from it and fills the
        figures from one probed report. Raises NotFound with no cloud
        providers."""
        rows, spending = domain_reports.get_balance(self._app, probe=probe)
        return BalanceReport(
            tuple(Balance(row.provider, row.label, row.money, row.note) for row in rows), spending
        )
