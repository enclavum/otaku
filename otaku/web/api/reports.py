"""The reports, as the page draws them from their facts: the context
window, the session's info, the usage and the balance — each the
backend's report object, `asdict`-ed."""

from dataclasses import asdict
from typing import Any

from otaku.backend import ModelInfo
from otaku.backend.api import reports
from otaku.backend.session import Refused, Session
from otaku.formatting import Money
from otaku.web.api.request import Ask, Route


def model_words(model: ModelInfo) -> dict[str, str]:
    """The info report's two rows on a model, in its words — how its
    thinking is set, and what it can do — which the page draws under
    the picker as it draws /info."""
    return {
        "reasoning_words": reports.reasoning_words(model.capabilities),
        "capability_words": reports.capability_words(model.capabilities),
    }


def context(session: Session) -> dict[str, Any]:
    """The next request: the shape the window diagram is drawn from, the
    summary, and one part per message. Nothing is rewritten — the page
    draws the role markers as the design draws them, around the report's
    own text."""
    report = reports.context(session)
    prompt = report.prompt
    return {
        # The numbers alone — never the wire, which `parts` carries — the
        # derived ones among them, so the page draws kept/total/used
        # exactly as the terminal says them.
        "shape": {
            "head": prompt.head,
            "middle": prompt.middle,
            "history": prompt.history,
            "rolled_up": prompt.scenes_rolled_up,
            "summaries": prompt.scenes_summarized,
            "tail": prompt.tail,
            "tail_target": prompt.tail_target,
            "tail_setting": report.tail_setting,
            "system_tokens": prompt.system_tokens,
            "transcript_tokens": prompt.transcript_tokens,
            "limit": prompt.limit,
            "pictures_sent": prompt.pictures_sent,
            "pictures_omitted": prompt.pictures_omitted,
            "pictures_held": prompt.pictures_held,
            "kept": report.kept,
            "total_tokens": prompt.total_tokens,
            "used": report.used,
        },
        "lede": report.summary,
        # What the preview could not know, in the report's words; "".
        "note": report.note,
        "parts": [asdict(part) for part in report.parts],
    }


def _usage(session: Session, raw: str = "") -> dict[str, Any]:
    """What the tokens were spent on, as the table's rows — and every
    scope the report can be asked for, because the page draws a tab per
    scope and needs them all to draw any.

    Refused — no story, nothing recorded, an argument that is not "all"
    — reaches the page as the sentence it is, BESIDE the scopes rather
    than instead of them: the scope that refused is the one the reader
    is on, and the other tab is how they get out of it."""
    scopes = [{"key": key, "label": label} for key, label in reports.USAGE_SCOPES]
    try:
        report = reports.usage(session, raw)
    except Refused as refusal:
        # Marked the way every decline is, even beside its scopes: one
        # refusal grammar, so the page reads a flag and never a wording.
        return {"notice": str(refusal), "refused": True, "scopes": scopes}
    return {
        "scope": report.scope,
        "scopes": scopes,
        # `label` rides along: what a purpose is CALLED is decided below
        # both frontends (`reports.USAGE_PURPOSES`), never here.
        "rows": [asdict(row) | {"label": row.purpose_label} for row in report.rows],
        "requests": report.requests,
        "prompt_tokens": report.prompt_tokens,
        "completion_tokens": report.completion_tokens,
        "cached_tokens": report.cached_tokens,
        "total_tokens": report.total_tokens,
        # The figures said in a sentence, and how much of the spend
        # nobody asked for — the report's own levels, not the page's.
        "note": report.note,
    }


def _money(money: Money | None) -> dict[str, Any] | None:
    """One amount on the wire: the figure as a STRING (a decimal is not
    a float and must not become one crossing JSON), its currency, and
    the rendering both frontends print."""
    if money is None:
        return None
    return {"amount": str(money.amount), "currency": money.currency, "text": str(money)}


def _balance(session: Session, ask: Ask) -> dict[str, Any]:
    """What each cloud account has left, as Money — and the note that
    stands where a figure would be for an account nobody has a key for
    or one that would not answer. `total` is everything on account when
    one currency covers every row, and null when it does not: adding
    across currencies is a conversion, and otaku has no rate.

    `?probe=none` returns the roster without asking any network (keyed
    rows carry an empty note — not asked yet): the page paints the whole
    slip from it, then fills the figures from one plain read."""
    report = reports.balances(session, probe=ask.query.get("probe") != "none")
    return {
        "rows": [
            {
                "provider": row.provider,
                "label": row.label,
                "money": _money(row.money),
                "note": row.note,
                "value": row.value,
            }
            for row in report.rows
        ],
        "total": _money(report.total),
        # What the story on screen spends — the report's own sentence.
        "note": report.note,
    }


def _info(session: Session) -> dict[str, Any]:
    """Everything otaku knows about this session, in the blocks the
    report is built from — labelled facts, or the sentence that stands
    where a block's facts would be."""
    return {
        "sections": [
            {"rows": [list(row) for row in section.rows], "note": section.note}
            for section in reports.info(session).sections
        ]
    }


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/session/context"): lambda session, ask: context(session),
    ("GET", "/api/session/info"): lambda session, ask: _info(session),
    ("GET", "/api/balance"): _balance,
    ("GET", "/api/usage"): lambda session, ask: _usage(session, ask.query.get("scope", "")),
}
