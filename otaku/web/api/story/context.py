"""A story's next request, as the page draws it from its facts: what the
request carries — the shape the window diagram is drawn from, the
summary, one part per message — and how full it is. The backend's
report object, translated here into the facts a page draws; `text()` is
the terminal's half and never crosses, but for the fill's one line."""

from dataclasses import asdict
from typing import Any

from otaku.backend.api import Api
from otaku.web.api._request import Ask, Route
from otaku.web.api.story.stories import tagged


def _context_fill(api: Api, ask: Ask) -> dict[str, Any]:
    """How full the story's next request is — the context report's own
    figure — and its sentence, which the rail's foot shows after every
    landing. Refused (404) while the model's window is not known."""
    fill = api.stories.context.fill.get(ask.id("story"))
    return {"percent": fill.percent, "context_in_force": fill.context_in_force, "text": fill.text()}


def _context(api: Api, ask: Ask) -> dict[str, Any]:
    """The story's next request: the shape the window diagram is drawn
    from, the summary, and one part per message. Nothing is rewritten —
    the page draws the role markers as the design draws them, around
    the report's own text."""
    report = api.stories.context.get(ask.id("story"))
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
            "total_tokens": report.total_tokens,
            "used": report.used,
        },
        "lede": report.summary,
        # What the preview could not know, in the report's words; "".
        "note": report.note,
        # What rides besides the story, each with its place named as the
        # depth ruler names it, and its cost.
        "injections": [
            {
                "label": injection.label,
                "position": injection.position.value,
                "position_text": injection.position_text,
                "tokens": injection.tokens,
            }
            for injection in report.injections
        ],
        "parts": [asdict(part) for part in report.parts],
    }


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/stories/{story}/context"): tagged(_context),
    ("GET", "/api/stories/{story}/context/fill"): tagged(_context_fill),
}
