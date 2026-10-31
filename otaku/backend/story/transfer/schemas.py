"""What `story.transfer.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema
from otaku.backend.story.lore.schemas import Extraction

# Re-exported: the frontends name an export's file, and the terminal
# refuses an export with no story the way the backend does.
from otaku.backend.story.transfer.services import EXPORT_SUFFIX as EXPORT_SUFFIX
from otaku.backend.story.transfer.services import NOTHING_TO_EXPORT as NOTHING_TO_EXPORT


class Import(Schema):
    """What an import landed: the story it made, the notices to show
    (counts, detected format), and — for the memoryless shapes — the
    forced extraction pass to wait on; a native export arrives with its
    memory and runs none."""

    story_id: int
    notices: tuple[str, ...]
    extraction: Extraction | None


class Export(Schema):
    """A story as its one Markdown document, and the file name it goes by
    (`the-long-road.md` from its title, `story.md` untitled)."""

    name: str
    text: str
