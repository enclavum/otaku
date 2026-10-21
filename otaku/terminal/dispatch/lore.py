"""A story's memory, typed, carried out — the twin of `backend.story.lore`:
the dossier opened on its scenes (`/lore`) or its cast (`/cast`), the
extraction pass run now and waited on in the foreground (`/extract`);
`/merge` is one call, its row the root's."""

import sys
import time

from otaku.terminal.api import NO_STORY_HINT, Refused
from otaku.terminal.api.lore import Extraction
from otaku.terminal.chat import landing
from otaku.terminal.chat.chat import Chat
from otaku.terminal.screens import dossier as screen_dossier
from otaku.terminal.screens.dossier.base import Tab
from otaku.terminal.tty import DIM, ERASE_LINE, RESET, error_line


def wait_extraction(chat: Chat, story_id: int) -> Extraction | None:
    """Wait on a story's forced pass in the foreground, read again every
    tenth of a second, the worker's progress on one updating (transient,
    self-erasing) line, opened by the model it asks as `/model` spells
    it; the pass as it ended, None when Ctrl+C cancelled it (the cancel
    notice said)."""
    shown = ""
    model = chat.api.app.get().extraction_model
    spec = f"{model.provider}/{model.name}" if model is not None else ""

    def transient(line: str) -> None:
        sys.stdout.write(f"\r{ERASE_LINE}{DIM}{line}{RESET}")
        sys.stdout.flush()

    sys.stdout.write(chat.ledger.said())
    transient(f"Closing a scene with {spec}…")
    try:
        while chat.api.stories.extraction.get(story_id).status == "running":
            time.sleep(0.1)
            line = chat.api.app.status.get()
            if line and line != shown:
                shown = line
                transient(line)
    except KeyboardInterrupt:
        try:
            transient(chat.api.stories.extraction.delete(story_id).notice)
        except Refused:
            pass  # it ended meanwhile: its report stands
        else:
            sys.stdout.write("\n")
            sys.stdout.flush()
            return None
    sys.stdout.write(f"\r{ERASE_LINE}")
    sys.stdout.flush()
    return chat.api.stories.extraction.get(story_id)


def lore(chat: Chat, raw: str) -> None:
    _dossier(chat, "scenes")


def cast(chat: Chat, raw: str) -> None:
    _dossier(chat, "cast")


def _dossier(chat: Chat, tab: Tab) -> None:
    """The open story's dossier, on the asked-for tab — an empty memory
    opens too (its tab says so itself; premise and messages are a Tab
    away). A landing from its messages tab echoes like the browser's."""
    if chat.story_id is None:
        chat.say(NO_STORY_HINT)
        return
    landing.land_from_screen(chat, screen_dossier.browse(chat.api, chat.story_id, tab))


def extract(chat: Chat, raw: str) -> None:
    """Schedule the forced pass, echo the worker's status on one updating
    line, Ctrl+C cancels; the report prints when the pass is done."""
    story_id = chat.played()
    chat.api.stories.extraction.create(story_id)
    done = wait_extraction(chat, story_id)
    if done is not None and done.report is not None:
        chat.say(error_line(done.report) if done.status == "failed" else done.report)
