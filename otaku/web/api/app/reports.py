"""The installation's reports, as the page draws them from their facts:
what the tokens were spent on, what otaku knows of this run and what
each cloud account has left — each the backend's report object,
translated here into the facts a page draws; `text()` is the terminal's
half and never crosses. A story's next request is the story's
(`story.context`)."""

from dataclasses import asdict
from typing import Any

from otaku.backend.api import Api
from otaku.web.api._request import Ask, Route, plain


def _usage(api: Api, ask: Ask) -> dict[str, Any]:
    """What the tokens were spent on — on the story the query names, on
    every story where it names none — as the table's rows. Nothing
    recorded is refused (404) in the backend's sentence."""
    report = api.usage.get(story_id=ask.query_id("story"))
    return {
        "scope": report.scope,
        # `purpose_label` rides along: what a purpose is CALLED is decided
        # below both frontends (`api.reports.USAGE_PURPOSES`), never here.
        "rows": [asdict(row) | {"purpose_label": row.purpose_label} for row in report.rows],
        "requests": report.requests,
        "prompt_tokens": report.prompt_tokens,
        "completion_tokens": report.completion_tokens,
        "cached_tokens": report.cached_tokens,
        "total_tokens": report.total_tokens,
        # The figures said in a sentence, and how much of the spend
        # nobody asked for — the report's own levels, not the page's.
        "note": report.note,
    }


def _balance(api: Api, ask: Ask) -> dict[str, Any]:
    """What each cloud account has left, as money (`plain`'s amount: a
    string, its currency, its text) — and the note that stands where a
    figure would be for an account nobody has a key for or one that
    would not answer. `total` is everything on account when one currency
    covers every row, and null when it does not: adding across
    currencies is a conversion, and otaku has no rate.

    `?probe=false` returns the roster without asking any network (keyed
    rows carry an empty note — not asked yet): the page paints the whole
    slip from it, then fills the figures from one plain read."""
    report = api.balance.get(probe=ask.query_flag("probe", True))
    return {
        "rows": [
            {
                "provider": row.provider,
                "label": row.label,
                "money": plain(row.money),
                "note": row.note,
                "value": row.value,
            }
            for row in report.rows
        ],
        "total": plain(report.total),
        # What the story on screen spends — the report's own sentence.
        "note": report.note,
    }


def _info(api: Api, ask: Ask) -> dict[str, Any]:
    """Everything otaku knows about this run — and about the story the
    query names — in the blocks the report is built from: labelled
    facts, or the sentence that stands where a block's facts would
    be."""
    return {
        "sections": [
            {"rows": [list(row) for row in section.rows], "note": section.note}
            for section in api.info.get(story_id=ask.query_id("story")).sections
        ]
    }


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/usage"): _usage,
    ("GET", "/api/info"): _info,
    ("GET", "/api/balance"): _balance,
}
