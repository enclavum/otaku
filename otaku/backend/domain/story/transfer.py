"""A story in and out as a document: an import writes the file's records
into a fresh story, then starts the extraction exactly like a forced
close — one path builds the memory whether the messages came from play
or from a file; an export renders the one Markdown document. The
document and its parsers live in `domain._formats`."""

import re
from datetime import datetime
from pathlib import PurePath

from otaku import __version__
from otaku.backend.domain._formats import EXPORT_FORMAT_VERSION, EXPORT_MARKER, exports, imports
from otaku.backend.domain._formats.plaintext import parse_plaintext
from otaku.backend.domain._formats.sillytavern import parse_sillytavern
from otaku.backend.domain.app import App
from otaku.backend.domain.story import lore
from otaku.backend.errors import NO_SUCH_STORY, Conflict, Invalid, NotFound, Refused

# What an export is: one Markdown document. A frontend that lets the user
# name the file supplies this where the name carries no extension of its
# own, so the suffix is spelled once for both of them.
EXPORT_SUFFIX = ".md"

# An export of a story with nothing played — no story at all included.
NOTHING_TO_EXPORT = "Nothing to export yet."


def import_story(
    app: App, text: str, file_name: str
) -> tuple[int, list[str], lore.ExtractionState | None]:
    """Import a story from a file's TEXT and NAME: an otaku export
    document (.md, its memory applied verbatim, no model calls), a
    SillyTavern chat (.jsonl), or plain text (.txt) dismantled into
    verbatim messages. The format is detected — never declared — and the
    file's name and contents must agree; a file that matches a format
    but fails its parser is refused, not degraded into prose.
    Content-shaped on purpose: reading a path is the frontend's, the web
    uploads. Writes a fresh story, and answers it with the notices to
    show (counts, detected format) and the forced pass to wait on — a
    native export arrives with its memory and runs none. Raises Refused
    with the exact reason (unknown format, a newer export — run `otaku
    update`)."""
    suffix = PurePath(file_name).suffix.lower()
    notices: list[str] = []
    native = False
    if suffix == ".md" and EXPORT_MARKER in text:
        try:
            export = imports.parse_story(text)
        except imports.NewerFormatError as e:
            raise Invalid(
                f"This export was written by a newer otaku (format {e.declared}; this version "
                f"reads up to {EXPORT_FORMAT_VERSION}) — run 'otaku update' first."
            ) from e
        if export is None:
            raise Invalid("This looks like an otaku export, but it does not parse.")
        native = True
    elif suffix == ".jsonl" and text.lstrip().startswith("{"):
        export = parse_sillytavern(text)
        if export is None:
            raise Invalid("This looks like JSON, but not a SillyTavern chat (.jsonl).")
        notices.append(f"SillyTavern chat: {len(export.messages)} message(s).")
    elif suffix == ".txt":
        export = parse_plaintext(text)
        if export is None:
            raise Invalid("The file contains no text to import.")
    else:
        raise Invalid("Cannot detect file format.")
    if not export.messages:
        raise Invalid("The file contains no messages to import.")

    story_id = imports.write_story(app._store, export)
    applied = f", {len(export.scenes)} scene(s) applied verbatim" if export.scenes else ""
    notices.append(f"Imported {len(export.messages)} message(s) → story {story_id}{applied}.")
    app._search_index = None
    # A native export carries its whole extraction state — including a
    # legitimately unextracted tail — so the story arrives exactly as it
    # was and extraction resumes its normal idle-gated life. The
    # memoryless shapes get their memory built now: the same forced pass,
    # waited on the same way, as a manual close. Without a model the
    # import still stands; the refusal joins the notices.
    run: lore.ExtractionState | None = None
    if not native:
        try:
            run, _ = lore.create_extraction(app, story_id)
        except Refused as e:
            notices.append(str(e))
    return story_id, notices, run


def export_story(app: App, story_id: int) -> tuple[str, str]:
    """A story as the one Markdown document: the story-so-far, system,
    and cast, the scenes with their journals, then every message
    verbatim (template included) — importable back losslessly — and the
    file name it goes by (`the-long-road.md` from its title, `story.md`
    untitled). Raises
    NotFound for a story that is not there, Conflict when there is
    nothing to export. Writing the file — and asking about overwrites —
    is the frontend's."""
    story = app._store.stories.get(story_id)
    if story is None:
        raise NotFound(NO_SUCH_STORY)
    if not app._store.stories.get_messages_ids(story_id):
        raise Conflict(NOTHING_TO_EXPORT)
    text = exports.render_story(
        exports.read_story(app._store, story_id),
        otaku_version=__version__,
        model=model.spec if (model := app._model()) is not None else "",
        exported=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
    )
    stem = re.sub(r"[^\w\s-]", "", story.title.lower())
    slug = re.sub(r"[\s_-]+", "-", stem).strip("-")
    return f"{slug or 'story'}{EXPORT_SUFFIX}", text
