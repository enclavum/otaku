"""Import/export commands: /import, /export, /copy.

`/import` writes the file's records into a fresh story, switches the
session onto it, and then triggers the extraction exactly like `/extract`
— one path builds the memory whether the messages came from play or from
a file. A full export that carries its memory needs no extraction; the
pass simply finds nothing to do. `/export` writes the story as the one
Markdown document the `transfer` package owns; `/copy` puts the last reply
(or a readable transcript) on the clipboard.
"""

import re
from datetime import datetime
from pathlib import Path

from otaku import __version__
from otaku.chat.commands import lore
from otaku.chat.session import Session
from otaku.store import Store
from otaku.terminal import YES_ANSWERS, clipboard, latin_key
from otaku.transfer import exports as story_exports
from otaku.transfer import imports as story_imports
from otaku.transfer.freetext import parse_freetext
from otaku.transfer.sillytavern import parse_sillytavern


def cmd_import(session: Session, store: Store, args: list[str]) -> None:
    """`/import chat FILE` — import a chat: an otaku /export file (its
    memory applied verbatim, no model calls) or a SillyTavern .jsonl.
    `/import text FILE` — dismantle a free-form text file into verbatim
    messages instead. A native export arrives with its extraction state
    and triggers nothing; the memoryless shapes (SillyTavern, text) have
    their memory built by the same forced extraction pass `/extract`
    runs, waited on in the foreground. The session switches to the
    imported story."""
    mode, _, rest = session.raw_args.partition(" ")
    path_text = rest.strip()
    if mode not in ("chat", "text") or not path_text:
        print("Usage: /import chat FILE  or  /import text FILE")
        return
    path = Path(path_text).expanduser()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"Could not read {path}: {e}")
        return

    native = False
    if mode == "text":
        export = parse_freetext(text)
    elif (export := story_imports.parse_story(text)) is not None:
        native = True
    elif (export := parse_sillytavern(text)) is not None:
        print(f"SillyTavern chat: {len(export.messages)} message(s).")
    else:
        print("Not an otaku export or a SillyTavern chat (.jsonl).")
        return
    if export is None:
        print("The file contains no text to import.")
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
    `/import chat`, losslessly. No name writes `<story-title>.md` (or
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
        except EOFError, KeyboardInterrupt:
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


def cmd_copy(session: Session, store: Store, args: list[str]) -> None:
    """`/copy` — the last model reply to the clipboard; `/copy all` — the
    whole transcript as readable Markdown (for pasting elsewhere; round
    trips go through /export files). A native clipboard tool when one
    exists, else the OSC 52 terminal escape."""
    if args and args[0].lower() != "all":
        print("Usage: /copy [all]")
        return
    if not session.messages:
        print("Nothing to copy.")
        return
    if args:
        text, what = _transcript_markdown(session), "transcript"
    else:
        reply = next((m.body for m in reversed(session.messages) if m.role == "assistant"), "")
        if not reply:
            print("Nothing to copy (no assistant reply yet).")
            return
        text, what = reply, "last reply"
    method = clipboard.copy(text)
    suffix = " (via OSC 52)" if method == "osc52" else ""
    print(f"Copied {what} to clipboard ({len(text):,} chars){suffix}.")


def _default_filename(session: Session, store: Store) -> str:
    """`the-long-road.md` from the story title; `story.md` when untitled."""
    story = store.stories.get(session.story_id) if session.story_id is not None else None
    stem = re.sub(r"[^\w\s-]", "", (story.title if story else "").lower())
    slug = re.sub(r"[\s_-]+", "-", stem).strip("-")
    return f"{slug or 'story'}.md"


def _transcript_markdown(session: Session) -> str:
    """The context as a readable `## role` transcript — bodies only, with
    the speaker or an ooc mark in the header."""
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [f"# {session.full_model_name} · {stamp}", ""]
    for message in session.messages:
        if message.kind == "ooc":
            label = f"{message.role} (ooc)"
        elif message.speaker:
            label = f"{message.role} ({message.speaker})"
        else:
            label = message.role
        lines += [f"## {label}", "", message.body, ""]
    return "\n".join(lines).rstrip() + "\n"
