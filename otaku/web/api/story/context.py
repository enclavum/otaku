"""A story's next request, as the page draws it from its facts: what the
request carries — the shape the window diagram is drawn from, the
summary, its text in parts, each with where it comes from — and how
full it is. The backend's
report object, translated here into the facts a page draws; `text()` is
the terminal's half and never crosses, but for the fill's one line."""

from typing import Any

from otaku.backend.api import Api
from otaku.backend.story.context.schemas import ContextSeparator
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
    from, the summary, and its text in parts, each with where it comes
    from. Nothing is rewritten — the page draws the markers as the design
    draws them, around the report's own text."""
    report = api.stories.context.get(ask.id("story"))
    figures = report.figures
    return {
        # The numbers alone — never the wire, which `parts` carries — the
        # derived ones among them, so the page draws kept/total/used
        # exactly as the terminal says them.
        "shape": {
            "head": figures.head,
            "middle": figures.middle,
            "history": figures.history_sent,
            "rolled_up": figures.scenes_rolled_up,
            "summaries": figures.scenes_summarized,
            "tail": figures.tail,
            "tail_target": figures.tail_target,
            "tail_setting": figures.tail_setting,
            "system_tokens": figures.system_tokens,
            "transcript_tokens": figures.transcript_tokens,
            "limit": figures.limit,
            "pictures_sent": figures.pictures_sent,
            "pictures_omitted": figures.pictures_omitted,
            "pictures_held": figures.pictures_held,
            "kept": figures.kept,
            "total_tokens": figures.total_tokens,
            "used": figures.used,
        },
        "lede": report.summary,
        # What the preview could not know, in the report's words; "".
        "note": report.note,
        # The character cards it carries, each with its portrait counted.
        "cards": [{"name": card.name, "tokens": card.tokens} for card in report.cards],
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
        # The text in parts, each with where it comes from, a separator
        # between two that stand apart; a scene's number and title, an
        # injection's name and place, a card's character — "" and null
        # where they do not apply.
        "parts": [
            {"kind": "separator"}
            if isinstance(part, ContextSeparator)
            else {
                "kind": "part",
                "role": part.role,
                "body": part.body,
                "pictures": part.pictures,
                "provenance": part.provenance.value,
                "scene_num": part.scene_num,
                "scene_title": part.scene_title,
                "injection_label": part.injection_label,
                "injection_position": (
                    part.injection_position.value if part.injection_position is not None else None
                ),
                "injection_position_text": part.injection_position_text,
                "card_name": part.card_name,
                "caption": part.caption,
            }
            for part in report.parts
        ],
    }


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/stories/{story}/context"): tagged(_context),
    ("GET", "/api/stories/{story}/context/fill"): tagged(_context_fill),
}
