"""Stories crossing otaku's edge, as the page asks: a story exported as
one document, and one landed from an uploaded document — an otaku
export, a SillyTavern chat, or plain text — which is the document half
of the new-story flow (`stories`), here because the reading is
`backend.api.transfer`'s."""

from otaku.backend.api import transfer as api_transfer
from otaku.backend.session import Session
from otaku.web.api.request import Created, Pending, Route, landed_story


def export_document(session: Session, story_id: int | None = None) -> dict[str, str]:
    """A story as one Markdown document, and the name to save it under
    — `story_id` for one that is not open, so the browser can export
    without landing on it first. Writing the file is the page's: a
    browser saves where the reader says, and the server never learns
    where."""
    return {
        "name": api_transfer.export_name(session, story_id),
        "text": api_transfer.export(session, story_id),
    }


def imported(session: Session, pending: Pending, text: str, name: str) -> Created:
    """A story read out of an uploaded document. Content-shaped by
    contract: a path over HTTP would name a file on the SERVER, so the
    page sends what it read. The extraction pass the memoryless shapes
    start is kept where a forced pass is kept, so the page polls one
    place for both; a native export arrives with its memory and runs
    none — `watching` says so, which is what stops the page polling for
    a report never coming."""
    landed = api_transfer.import_file(session, text, name)
    # Only an import that STARTED a pass takes a slot — and its own
    # story's slot, so a forced pass the page is polling on another
    # story keeps its run and its report.
    if landed.extraction is not None and session.story_id is not None:
        pending.extraction[session.story_id] = landed.extraction
    return Created(
        " ".join(landed.notices),
        landed_story(session),
        # And whether a pass is running on it, which is what stops the
        # page polling for a report that is never coming.
        {"story": session.story_id, "watching": landed.extraction is not None},
    )


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/stories/{story}/export"): lambda session, ask: export_document(
        session, ask.id("story")
    ),
}
