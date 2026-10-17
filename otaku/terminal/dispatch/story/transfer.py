"""Stories crossing otaku's edge, typed, carried out — the twin of
`api.story.transfer`: a document imported as a story (`/import FILE`),
its extraction pass waited on, and the story exported as one (`/export
[FILE]`) — the file read and written on this side."""

import sys
from collections.abc import Callable
from pathlib import Path

from otaku.terminal.api import Conflict
from otaku.terminal.api.story.transfer import EXPORT_SUFFIX, NOTHING_TO_EXPORT
from otaku.terminal.chat.chat import RESUME_TURNS, Chat
from otaku.terminal.dispatch import _files
from otaku.terminal.dispatch.story import lore
from otaku.terminal.tty import YES_ANSWERS, error_line, latin_key
from otaku.terminal.tty.render import last_turns


def _import(chat: Chat, raw: str) -> None:
    """Resolve and read the file on THIS side (`@` stripped), then the
    import (`api.imports`); wait on the extraction pass showing the
    worker's line, then echo the landing under the break rule — as deep
    in the scene as any other way in."""
    text = raw.strip().removeprefix("@")
    if not text:
        chat.say("Usage: /import FILE")
        return
    path = Path(text).expanduser()
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        chat.say(error_line(f"Could not read {path}: {e}"))
        return
    result = chat.api.imports.create(text=content, file_name=path.name).value
    chat.land(result.story_id)
    for notice in result.notices:
        chat.say(notice)
    if result.extraction is not None:
        sys.stdout.write("\n")  # the import's counts, then its extraction, apart
        done = lore.wait_extraction(chat, result.story_id)
        if done is not None and done.report is not None:
            chat.say(done.report)
    chat.ledger.rule()
    turns = last_turns(
        chat.messages(),
        RESUME_TURNS,
        shown=chat.shown_tools,
        is_story_mode=chat.is_story_mode,
        posed=chat.posed,
    )
    chat.say(f"The last turns of this story:\n\n{turns}")
    chat.restore_tail(RESUME_TURNS)


def _export(chat: Chat, raw: str) -> None:
    """Render the document, confirm an overwrite, write the file. No
    name writes `<story-title>.md` (or story.md) in the current
    directory; a name carrying no extension of its own gets `.md`, since
    the document is one; an existing file prompts before overwriting
    (default no). A leading `@` — the path-completion trigger — is not
    part of the name."""
    if chat.story_id is None:
        raise Conflict(NOTHING_TO_EXPORT)
    exported = chat.api.stories.export.get(chat.story_id)
    document = exported.text
    name = raw.strip().removeprefix("@")
    path = Path(name).expanduser() if name else Path(exported.name)
    # An existing DIRECTORY keeps its name: `.md` would write a file
    # beside it, where the write refusing out loud is the honest answer.
    if not path.suffix and not path.is_dir():
        path = path.with_suffix(EXPORT_SUFFIX)
    # A name the filesystem will not even look up (too long, say) is not
    # an existing file: fall through, and let the write refuse out loud.
    if _files.existing_file(str(path)) is not None:
        sys.stdout.write(chat.ledger.said())
        try:
            answer = latin_key(input(f"{path} already exists — overwrite? [y/N] ").strip())
        except (EOFError, KeyboardInterrupt):
            chat.say("Cancelled.")
            return
        if answer not in YES_ANSWERS:
            chat.say("Not exported.")
            return
    try:
        path.write_text(document, encoding="utf-8")
    except OSError as e:
        chat.say(error_line(f"Could not write {path}: {e}"))
        return
    chat.say(f"Exported to {path}.")


# ---------- the rows ----------


HANDLERS: dict[str, Callable[[Chat, str], str | None]] = {
    "/import": _import,
    "/export": _export,
}
