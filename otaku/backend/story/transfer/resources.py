"""A story in and out as a document: an import, which makes a fresh story
and starts its memory's pass (`ImportsResource`), and an export, the one
Markdown document (`ExportResource`). The work is the domain's
(`story.transfer.services`)."""

from __future__ import annotations

from otaku.backend.base import Changed, Resource
from otaku.backend.story.lore.resources import ExtractionResource
from otaku.backend.story.transfer import services as domain_transfer
from otaku.backend.story.transfer.schemas import Export, Import


class ImportsResource(Resource):
    """/imports — a story made from a document."""

    def create(self, *, text: str, file_name: str) -> Changed[Import]:
        """Import a story from a file's TEXT and NAME: an otaku export
        document (.md, its memory applied verbatim, no model calls), a
        SillyTavern chat (.jsonl), or plain text (.txt) dismantled into
        verbatim messages. The format is detected — never declared — and
        the file's name and contents must agree. Content-shaped on
        purpose: reading a path is the frontend's, the web uploads. The
        memoryless shapes get their memory's pass started. Raises Refused
        with the exact reason."""
        story_id, notices, run = domain_transfer.import_story(self._app, text, file_name)
        extraction = ExtractionResource(self._app).get(story_id) if run is not None else None
        imported = Import(story_id=story_id, notices=tuple(notices), extraction=extraction)
        return Changed(imported, " ".join(notices))


class ExportResource(Resource):
    """/stories/{story}/export — a story as its one document."""

    def get(self, story_id: int) -> Export:
        """A story as the one Markdown document — importable back
        losslessly. Raises NotFound for a story that is not there,
        Conflict when there is nothing to export. Writing the file — and
        asking about overwrites — is the frontend's."""
        name, text = domain_transfer.export_story(self._app, story_id)
        return Export(name, text)
