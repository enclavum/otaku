"""Import/export commands: /import, /export.

`/import` writes the file's records into a fresh story, switches the
session onto it, and then triggers the extraction exactly like `/extract`
— one path builds the memory whether the messages came from play or from
a file. A full export that carries its memory needs no extraction; the
pass simply finds nothing to do. `/export` writes the story as the one
Markdown document the `transfer` package owns.
"""

import re
from datetime import datetime
from pathlib import Path

from otaku import __version__
from otaku.chat.commands import lore
from otaku.chat.session import Session
from otaku.store import Store
from otaku.terminal import YES_ANSWERS, latin_key
from otaku.transfer import EXPORT_MARKER
from otaku.transfer import exports as story_exports
from otaku.transfer import imports as story_imports
from otaku.transfer.plaintext import parse_plaintext
from otaku.transfer.sillytavern import parse_sillytavern


def cmd_import(session: Session, store: Store, args: list[str]) -> None:
    """`/import FILE` — import a story, the format detected from the
    file's name and contents: an otaku /export document (.md, its memory
    applied verbatim, no model calls), a SillyTavern chat (.jsonl), or
    plain text (.txt) dismantled into verbatim messages. A native export
    arrives with its extraction state and triggers nothing; the
    memoryless shapes (SillyTavern, plain text) have their memory built
    by the same forced extraction pass `/extract` runs, waited on in the
    foreground. The session switches to the imported story."""
    path_text = session.raw_args.strip()
    if not path_text:
        print("Usage: /import FILE")
        return
    path = Path(path_text).expanduser()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"Could not read {path}: {e}")
        return

    # The format is detected — never declared — and the file's NAME and
    # contents must agree. A file that matches a format but fails its
    # parser is refused, not degraded into prose.
    suffix = path.suffix.lower()
    native = False
    if suffix == ".md" and EXPORT_MARKER in text:
        export = story_imports.parse_story(text)
        if export is None:
            print("This looks like an otaku export, but it does not parse.")
            return
        native = True
    elif suffix == ".jsonl" and text.lstrip().startswith("{"):
        export = parse_sillytavern(text)
        if export is None:
            print("This looks like JSON, but not a SillyTavern chat (.jsonl).")
            return
        print(f"SillyTavern chat: {len(export.messages)} message(s).")
    elif suffix == ".txt":
        export = parse_plaintext(text)
        if export is None:
            print("The file contains no text to import.")
            return
    else:
        print("Cannot detect file format.")
        return
    if not export.messages:
        print("The file contains no messages to import.")
        return

    story_id = story_imports.write_story(store, export)
    applied = f", {len(export.scenes)} scene(s) applied verbatim" if export.scenes else ""
    print(f"Imported {len(export.messages)} message(s) → story {story_id}{applied}.")

    session.switch_to(store, story_id)
    # A native export carries its whole extraction state — including a
    # legitimately unextracted tail — so the story arrives exactly as it
    # was and extraction resumes its normal idle-gated life. The
    # memoryless shapes get their memory built now: the same forced pass,
    # waited on the same way, as typing /extract.
    if not native:
        lore.cmd_extract(session, store, [])


def cmd_export(session: Session, store: Store, args: list[str]) -> None:
    """`/export [FILE]` — the current story as one Markdown document: the
    story-so-far, system, and cast, the scenes with their journals, then
    every message verbatim (framing included) — importable back with
    `/import`, losslessly. No name writes `<story-title>.md` (or
    story.md) in the current directory; an existing file prompts before
    overwriting (default no)."""
    if not session.messages:
        print("Nothing to export yet.")
        return
    story_id = session.ensure_story(store)
    doc = story_exports.render_story(
        story_exports.read_story(store, story_id),
        otaku_version=__version__,
        model=session.full_model_name,
        exported=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
    )
    name = session.raw_args.strip()
    path = Path(name).expanduser() if name else Path(_default_filename(session, store))
    if path.exists():
        try:
            answer = latin_key(input(f"{path} already exists — overwrite? [y/N] ").strip())
        except (EOFError, KeyboardInterrupt):
            print("Cancelled.")
            return
        if answer not in YES_ANSWERS:
            print("Not exported.")
            return
    try:
        path.write_text(doc, encoding="utf-8")
    except OSError as e:
        print(f"Could not write {path}: {e}")
        return
    print(f"Exported to {path}.")


def _default_filename(session: Session, store: Store) -> str:
    """`the-long-road.md` from the story title; `story.md` when untitled."""
    story = store.stories.get(session.story_id) if session.story_id is not None else None
    stem = re.sub(r"[^\w\s-]", "", (story.title if story else "").lower())
    slug = re.sub(r"[\s_-]+", "-", stem).strip("-")
    return f"{slug or 'story'}.md"
