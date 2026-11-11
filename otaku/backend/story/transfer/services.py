"""A story in and out as a document: an import writes the file's records
into a fresh story, then starts the extraction exactly like a forced
close — one path builds the memory whether the messages came from play
or from a file; an export renders the one Markdown document. The
document and its parsers live in `backend.formats.stories`."""

import re
from datetime import datetime

from otaku import __version__
from otaku.backend.app.core.services import App
from otaku.backend.errors import NO_SUCH_STORY, Conflict, Invalid, NotFound, Refused
from otaku.backend.formats.stories import EXPORT_FORMAT_VERSION, exports, imports
from otaku.backend.story.lore import services as lore
from otaku.settings.state import ModelRole

# What an export is: one Markdown document. A frontend that lets the user
# name the file supplies this where the name carries no extension of its
# own, so the suffix is spelled once for both of them.
EXPORT_SUFFIX = ".md"

# An export of a story with nothing played — no story at all included.
NOTHING_TO_EXPORT = "Nothing to export yet."

# A file its format claims but its reader finds no story in, by its format.
_UNREADABLE: dict[imports.StoryFileKind, str] = {
    "document": "This looks like an otaku export, but it does not parse.",
    "sillytavern": "This looks like JSON, but not a SillyTavern chat (.jsonl).",
    "plaintext": "The file contains no text to import.",
}


def import_story(
    app: App, text: str, file_name: str
) -> tuple[int, list[str], lore.ExtractionState | None]:
    """Import a story from a file's TEXT and NAME, read by the format they
    agree on (`formats.stories.imports.read_story_file`): an otaku export
    document, its memory applied verbatim with no model calls, a
    SillyTavern chat, or plain text dismantled into verbatim messages.
    Content-shaped on purpose: reading a path is the frontend's, the web
    uploads. Writes a fresh story, and answers it with the notices to
    show (counts, detected format) and the forced pass to wait on — a
    native export arrives with its memory and runs none. Raises Refused
    with the exact reason (unknown format, a file its format's reader
    cannot read, a newer export — run `otaku update`)."""
    notices: list[str] = []
    try:
        export, kind = imports.read_story_file(text, file_name)
    except imports.NewerFormatError as e:
        raise Invalid(
            f"This export was written by a newer otaku (format {e.declared}; this version "
            f"reads up to {EXPORT_FORMAT_VERSION}) — run 'otaku update' first."
        ) from e
    except imports.UnknownFormatError:
        raise Invalid("Cannot detect file format.") from None
    except imports.UnreadableFileError as e:
        raise Invalid(_UNREADABLE[e.kind]) from None
    native = kind == "document"
    if kind == "sillytavern":
        notices.append(f"SillyTavern chat: {len(export.messages)} message(s).")
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
        model=model.spec if (model := app._model(ModelRole.WRITING)) is not None else "",
        exported=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
    )
    stem = re.sub(r"[^\w\s-]", "", story.title.lower())
    slug = re.sub(r"[\s_-]+", "-", stem).strip("-")
    return f"{slug or 'story'}{EXPORT_SUFFIX}", text
