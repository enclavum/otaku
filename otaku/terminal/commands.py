"""The terminal's commands: one table declaring every slash word the
terminal answers to — its token, what it takes, what /help calls it and
the group it is filed under — the declaration, not the dispatch — and
the keys that type a command for the reader (`SHORTCUTS`). A spec's
`kind` says what its handler is. OPERATION is one call: the handler
reads the typed argument, makes the interface call and answers the
sentence. INTERACTIVE is a sequence the terminal owns — a screen, an
ask, paging — every state touch still an interface call. SYNTAX is the story's language, typed inside a played
line and read by the backend at play: its tokens and argument shapes are
the backend's (`backend.story.language`), its captions this table's.

Nothing here is shared: the page has no commands — a button calls an
endpoint — and what both frontends say alike is the language's alone. A
leaf every room of the terminal reads: the prompt's menus, the
renderer's pick-out, the dispatch and /help.
"""

import enum
import sys
from dataclasses import dataclass
from typing import NamedTuple

from otaku.terminal.api import language as api_language

# The row /help opens the playing group with — prose is the surface's
# real first row. It is not a command and has no spec: what a help page
# needs is the label to give it, the group it opens, and the language's
# own sentence (`language.PROSE_DESCRIPTION`).
PROSE_LABEL = "PROMPT"
PROSE_GROUP = "playing"

# What each group is called, in the table's own order. Every group has a
# name: a heading is how a reader finds a command they cannot spell, and
# the two rows about otaku itself are no less findable than the rest.
GROUP_LABELS: dict[str, str] = {
    "playing": "Playing",
    "inline": "Inside a prompt",
    "stories": "Stories",
    "lore": "Lore",
    "inspect": "Inspect",
    "transfer": "Import/export",
    "settings": "Models and settings",
    "special": "Special",
    "meta": "Meta",
}


class CommandType(enum.Enum):
    """The separation the terminal routes by (see the module docstring)."""

    OPERATION = "operation"
    INTERACTIVE = "interactive"
    SYNTAX = "syntax"


@dataclass(frozen=True)
class CommandSpec:
    """One row of the surface. `args` is the argument shape as /help
    spells it ("NAME[: HINT]", "[N]"); a bracketed shape is optional and
    the bare command is complete as it stands."""

    token: str
    args: str
    description: str
    group: str
    kind: CommandType


class Shortcut(NamedTuple):
    """One key in both spellings: what prompt_toolkit binds and what the
    menus print."""

    key: str  # prompt_toolkit's name ("c-r")
    caption: str  # the human's ("Ctrl+R")


# The language's words by token, for the SYNTAX rows below.
_LANGUAGE = {row.token: row for row in (*api_language.OPENERS, *api_language.INLINERS)}


def _syntax(token: str, description: str, group: str = "playing") -> CommandSpec:
    """A row of the story's language: token and argument shape the
    backend's, the caption this table's — a token the language does not
    have fails at import."""
    row = _LANGUAGE[token]
    return CommandSpec(row.token, row.args, description, group, CommandType.SYNTAX)


# fmt: off
COMMANDS: tuple[CommandSpec, ...] = (
    # Playing
    _syntax("/me", "Send PROMPT, hinting the model that you write as NAME"),
    _syntax("/you", "Tell the model to play NAME; optional HINT rides the turn"),
    _syntax("/ooc", "Talk to the model out of character"),
    CommandSpec("/undo", "", "Discard the last turn", "playing", CommandType.INTERACTIVE),
    # The mid-stream take is the terminal's POSIX-only watcher — the
    # Windows consoles have no termios, so there the row must not say more.
    CommandSpec("/regen", "", "Re-run the last prompt" + ("" if sys.platform == "win32" else " (mid-stream: cancel + regen)"), "playing", CommandType.INTERACTIVE),
    CommandSpec("/last", "[N]", "Show the last N turns (default 5)", "playing", CommandType.INTERACTIVE),
    CommandSpec("/clear", "", "Clear the screen", "playing", CommandType.INTERACTIVE),
    # Inside a prompt
    _syntax("… /ooc", "An aside out of character", "inline"),
    _syntax("… /cue", "Steer just the next reply — not kept in context afterwards", "inline"),
    # Stories
    CommandSpec("/stories", "", "Browse stories, resume an old one", "stories", CommandType.INTERACTIVE),
    CommandSpec("/fork", "[TITLE]", "Continue in a copy of this story; the original stays", "stories", CommandType.OPERATION),
    CommandSpec("/system", "<text | FILE>", "Set the system prompt for this story — from text or a file; - clears it", "stories", CommandType.INTERACTIVE),
    CommandSpec("/title", "NEW", "Set the story title", "stories", CommandType.OPERATION),
    CommandSpec("/new", "[TITLE]", "Clear context and start a new story", "stories", CommandType.OPERATION),
    # Lore
    CommandSpec("/lore", "", "Browse and edit the story — premise, messages, scenes, cast", "lore", CommandType.INTERACTIVE),
    CommandSpec("/cast", "", "The same browser, opened directly on the cast", "lore", CommandType.INTERACTIVE),
    CommandSpec("/extract", "", "Extract lore from the recent messages now; triggered automatically after 5 minutes of inactivity", "lore", CommandType.INTERACTIVE),
    CommandSpec("/merge", "A into B", "Fold a duplicate character into the real one", "lore", CommandType.OPERATION),
    # Inspect
    CommandSpec("/context", "", "Preview the next request (assembled prompt + budgets)", "inspect", CommandType.INTERACTIVE),
    CommandSpec("/balance", "", "Account balance of cloud providers", "inspect", CommandType.OPERATION),
    CommandSpec("/usage", "[all]", "Tokens spent on this story (or everything)", "inspect", CommandType.OPERATION),
    CommandSpec("/info", "", "Show details about the current model + session", "inspect", CommandType.OPERATION),
    # Import/export
    CommandSpec("/card", "FILE [NAME]", "Import a character card (PNG or JSON) into this story", "transfer", CommandType.INTERACTIVE),
    CommandSpec("/import", "FILE", "Import a story: an otaku export, SillyTavern chat (.jsonl), or plain text", "transfer", CommandType.INTERACTIVE),
    CommandSpec("/export", "[FILE]", "Export the whole story to Markdown (memory + messages)", "transfer", CommandType.INTERACTIVE),
    # Models and settings
    CommandSpec("/model", "[PROVIDER/MODEL]", "Switch model", "settings", CommandType.INTERACTIVE),
    CommandSpec("/set parameter", "<name> <val>", "Set an inference parameter for the model", "settings", CommandType.OPERATION),
    CommandSpec("/set mode", "chat|story", "Read the story as a chat, or as one text", "settings", CommandType.OPERATION),
    CommandSpec("/set think", "<level>", "Set the thinking effort for the model, or a thinking budget in tokens where supported", "settings", CommandType.OPERATION),
    CommandSpec("/set verbose", "on|off", "Show the stats line after each reply", "settings", CommandType.OPERATION),
    CommandSpec("/set autocorrect", "on|off", "Settle a character name you type in /you and /me commands to the cast's own spelling", "settings", CommandType.OPERATION),
    CommandSpec("/set notification", "on|off", "Play a sound when a reply lands (the sound is configs/config.toml's notification_sound)", "settings", CommandType.OPERATION),
    CommandSpec("/set max_context", "<tokens>", "Cap the prompt at this many tokens; 0 = the model's own max context", "settings", CommandType.OPERATION),
    CommandSpec("/set serve", "on|off", "Serve the web interface while the terminal runs", "settings", CommandType.OPERATION),
    # Special
    _syntax("/roll", "Roll real dice (1d20+5, 3d6, 2d20kh1) — the model narrates exactly what fell", "special"),
    # Meta
    CommandSpec("/support", "TEXT", "Send feedback or a support request — leave your email to get an answer", "meta", CommandType.OPERATION),
    CommandSpec("/help", "", "Show this help", "meta", CommandType.INTERACTIVE),
    CommandSpec("/quit", "", "Exit", "meta", CommandType.INTERACTIVE),
)
# fmt: on

# Other names a command answers to, hidden: typed, each runs its command,
# but no menu and no /help offers it.
ALIASES: dict[str, str] = {"/bye": "/quit", "/exit": "/quit"}

# The terminal's shortcut keys, by the token they run.
SHORTCUTS: dict[str, Shortcut] = {
    "/regen": Shortcut("c-r", "Ctrl+R"),
    "/undo": Shortcut("c-u", "Ctrl+U"),
    "/stories": Shortcut("c-t", "Ctrl+T"),
    "/lore": Shortcut("c-l", "Ctrl+L"),
    "/model": Shortcut("c-o", "Ctrl+O"),  # Ctrl+M is unusable — the terminal sends it as Enter
    "/quit": Shortcut("c-d", "Ctrl+D"),
}


def is_command(line: str) -> bool:
    """Whether a typed line is the terminal's to answer rather than the
    story's: it opens with a slash word that opens nothing in the
    story's language."""
    return line.startswith("/") and not api_language.opener(line)
