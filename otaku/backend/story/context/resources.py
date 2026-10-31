"""A story's next request as a frontend reaches it: the request
previewed (`ContextResource`) and how full it is (`ContextFillResource`).
What a resource answers is the report — the facts it is made of, and
`text()`, the same facts as a terminal pages them — as every report is
(`api.app.reports`). The facts are gathered by the domain
(`story.context.services`)."""

from __future__ import annotations

from otaku.backend.app.core.services import App
from otaku.backend.base import Resource
from otaku.backend.errors import NotFound
from otaku.backend.story.context import services as domain_context
from otaku.backend.story.context.schemas import ContextFill, ContextReport, as_context_report


class ContextResource(Resource):
    """/stories/{story}/context — a story's next request, previewed."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.fill = ContextFillResource(app)

    def get(self, story_id: int) -> ContextReport:
        """Preview a story's next request: what it is made of, said in
        words, and its text in parts, each with where it comes from, a
        separator between two that stand apart. No model: the
        preview still stands, over the assembler's default window — what
        WOULD be sent is a question that needs no server. A model not
        loaded is not loaded for a preview, so its window is the
        substitute too, and the report says so. Raises NotFound for a
        story that is not there, Conflict for one that cannot fit."""
        return as_context_report(domain_context.get_context(self._app, story_id))


class ContextFillResource(Resource):
    """/stories/{story}/context/fill — how full a story's next request
    is."""

    def get(self, story_id: int) -> ContextFill:
        """How full a story's next request is — the context report's own
        figure, read after every turn. Never waits on the internet.
        Raises NotFound while the window is not known (no model, a model
        not loaded, a catalog that has not answered) — a figure against
        the assembler's stand-in would mislead — and for a story that is
        not there."""
        found = domain_context.get_context_fill(self._app, story_id)
        if found is None:
            raise NotFound("The model's window is not known yet.")
        return ContextFill(*found)
