"""A story in and out as a document: an import, which makes a fresh story
and starts its memory's pass (`ImportsResource`), and an export, the one
Markdown document (`ExportResource`). The work is the domain's
(`domain.story.transfer`)."""

from __future__ import annotations

from dataclasses import dataclass

from otaku.backend.api._writes import Changed
from otaku.backend.api.story.lore import Extraction, ExtractionResource
from otaku.backend.domain.app import App
from otaku.backend.domain.story import transfer as domain_transfer

# Re-exported: the frontends name an export's file, and the terminal
# refuses an export with no story the way the backend does.
from otaku.backend.domain.story.transfer import EXPORT_SUFFIX as EXPORT_SUFFIX
from otaku.backend.domain.story.transfer import NOTHING_TO_EXPORT as NOTHING_TO_EXPORT


class ImportsResource:
    """/imports — a story made from a document."""

    def __init__(self, app: App) -> None:
        self._app = app

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


class ExportResource:
    """/stories/{story}/export — a story as its one document."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, story_id: int) -> Export:
        """A story as the one Markdown document — importable back
        losslessly. Raises NotFound for a story that is not there,
        Conflict when there is nothing to export. Writing the file — and
        asking about overwrites — is the frontend's."""
        name, text = domain_transfer.export_story(self._app, story_id)
        return Export(name, text)


@dataclass(frozen=True)
class Import:
    """What an import landed: the story it made, the notices to show
    (counts, detected format), and — for the memoryless shapes — the
    forced extraction pass to wait on; a native export arrives with its
    memory and runs none."""

    story_id: int
    notices: tuple[str, ...]
    extraction: Extraction | None


@dataclass(frozen=True)
class Export:
    """A story as its one Markdown document, and the file name it goes by
    (`the-long-road.md` from its title, `story.md` untitled)."""

    name: str
    text: str
