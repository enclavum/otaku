"""Slash commands: the dispatch table, the completion tree, the help text.

`COMMANDS` pairs each command with its handler and its tab-completion
subtree; the completer derives its tree from it and the descriptions from
`_HELP_ROWS`, so the menu, the completions, and /help can never disagree.
"""

from collections.abc import Callable

from otaku.chat.commands import inspect, lore, meta, playing, settings, stories, transfer
from otaku.chat.session import KNOWN_PARAMS, Session
from otaku.store import Store

# A completion-tree leaf may be PATH_LEAF: "complete a filesystem path
# here" (behind an `@`; see completer.py). Paths may contain spaces —
# handlers read them from raw_args, never from split args, and strip the
# leading `@`.
PATH_LEAF = "<path>"

CompletionTree = dict[str, "CompletionTree | str | None"]

CommandHandler = Callable[[Session, Store, list[str]], None]


# (command, shortcut, description) per row; "" for no shortcut, None starts
# a group heading. One row per source line, whatever its width — E501 is
# off for this file (see pyproject) and the formatter is off for the table.
# fmt: off
_HELP_ROWS: list[tuple[str | None, str, str]] = [
    (None, "", "Playing:"),
    ("PROMPT", "", "Your character speaks or acts; the model continues the scene — sent verbatim"),
    ("/me NAME: PROMPT", "", "Send PROMPT as NAME's line"),
    ("/you NAME", "", "The model is instructed to play NAME and responds"),
    ("/ooc PROMPT", "", "Talk to the model out of character"),
    ("/undo", "Ctrl+U", "Discard the last turn"),
    ("/regen", "Ctrl+R", "Re-run the last prompt (mid-stream: cancel + regen)"),
    ("/last [N]", "", "Show the last N turns (default 5) — a clean view after undos, regens, etc."),
    ("/clear", "", "Clear the screen"),
    (None, "", "Stories:"),
    ("/stories", "Ctrl+T", "Browse stories, resume an old one"),
    ("/fork [TITLE]", "", "Continue in a copy of this story; the original stays"),
    ("/system <text | FILE>", "", "Set the system prompt for this story — either directly or from a file"),
    ("/rename NEW", "", "Set the story title"),
    ("/new", "", "Clear context and start a new story"),
    (None, "", "Lore:"),
    ("/lore", "Ctrl+L", "Browse and edit the memory: scenes, cast, journals"),
    ("/cast", "", "The same browser, opened directly on the cast"),
    ("/extract", "", "Extract lore from the recent messages now; triggered automatically after 5 minutes of inactivity"),
    ("/merge A into B", "", "Fold a duplicate character into the real one"),
    (None, "", "Inspect:"),
    ("/context", "", "Preview the next request (assembled prompt + budgets)"),
    ("/usage [all]", "", "Tokens spent on this story (or everything)"),
    ("/balance", "", "Account balance of cloud providers"),
    ("/info", "", "Show details about the current model + session"),
    (None, "", "Import/export:"),
    ("/import FILE", "", "Import a story: an otaku export, SillyTavern chat (.jsonl), or plain text"),
    ("/export [FILE]", "", "Export the whole story to Markdown (memory + messages)"),
    (None, "", "Model and settings:"),
    ("/model [PROVIDER/MODEL]", "Ctrl+O", "Switch model"),
    ("/set think <level>", "", "Thinking effort for the model: on|off|none|low|medium|high|max|default"),
    ("/set parameter <name> <val>", "", "Set an inference parameter for the model; no <val> shows it, <val> = reset returns the default"),
    ("/set verbose on|off", "", "Show the stats line after each reply"),
    ("", "", ""),
    ("/help", "", "Show this help"),
    ("/bye", "Ctrl+D", "Exit"),
    (None, "", "Keys at the prompt:"),
    ('"""', "", 'Begin a multiline message; close it with """'),
    ("@", "", "In a FILE argument: enable path autocompletion"),
    ("Up / Down", "", "Walk your recent prompt history"),
    ("Ctrl+C", "", "Clear the current line; cancel an in-flight reply"),
]
# fmt: on


def _build_help() -> str:
    cmd_width = max(len(row[0]) for row in _HELP_ROWS if row[0])
    key_width = max(len(row[1]) for row in _HELP_ROWS)
    lines: list[str] = []
    for command, key, desc in _HELP_ROWS:
        if command is None:  # a group heading — one blank line before it
            if lines:
                lines.append("")
            lines.append(desc)
            continue
        lines.append(f"  {command:<{cmd_width}} {key:<{key_width}}  {desc}".rstrip())
    return "\n".join(lines)


HELP_TEXT = _build_help()


def _set_tree() -> CompletionTree:
    """/set's completion subtree: each setting with its value menu."""
    levels = ("on", "off", "none", "low", "medium", "high", "max", "default")
    return {
        "think": {level: None for level in levels},
        "parameter": {p: {"reset": None} for p in KNOWN_PARAMS},
        "verbose": {"on": None, "off": None},
    }


# Each entry pairs the handler with its tab-completion subtree (None = no
# arguments), one entry per source line. Ordered as in _HELP_TEXT so the
# menu lists commands in the same order as the help.
# fmt: off
COMMANDS: dict[str, tuple[CommandHandler, CompletionTree | str | None]] = {
    "/me": (playing.cmd_me, None),
    "/you": (playing.cmd_you, None),
    "/ooc": (playing.cmd_ooc, None),
    "/undo": (playing.cmd_undo, None),
    "/regen": (playing.cmd_regen, None),
    "/last": (playing.cmd_last, None),
    "/clear": (playing.cmd_clear, None),
    "/stories": (stories.cmd_stories, None),
    "/fork": (stories.cmd_fork, None),
    "/system": (stories.cmd_system, PATH_LEAF),
    "/rename": (stories.cmd_rename, None),
    "/new": (stories.cmd_new, None),
    "/lore": (lore.cmd_lore, None),
    "/cast": (lore.cmd_cast, None),
    "/extract": (lore.cmd_extract, None),
    "/merge": (lore.cmd_merge, None),
    "/context": (inspect.cmd_context, None),
    "/usage": (inspect.cmd_usage, {"all": None}),
    "/balance": (inspect.cmd_balance, None),
    "/info": (inspect.cmd_info, None),
    "/import": (transfer.cmd_import, PATH_LEAF),
    "/export": (transfer.cmd_export, PATH_LEAF),
    "/model": (settings.cmd_model, None),
    "/set": (settings.cmd_set, _set_tree()),
    "/help": (meta.cmd_help, None),
    "/bye": (meta.cmd_bye, None),
}
# fmt: on


def completion_tree() -> CompletionTree:
    """The slash-completion tree, derived from COMMANDS — the completer can
    never drift from the dispatch table."""
    return {name: subtree for name, (_, subtree) in COMMANDS.items()}


def describe_command(tokens: tuple[str, ...]) -> str:
    """The help description for a command path — ("/set", "think") → its
    row — read from _HELP_ROWS so the menu and /help can never disagree."""
    for row in _HELP_ROWS:
        label = row[0]
        if label is None:
            continue
        parts = label.split()
        if len(parts) >= len(tokens) and tuple(parts[: len(tokens)]) == tokens:
            return row[2]
    return ""


# The playing commands manage the screen ledger themselves: three echo the
# turn as the grey block, two take one back. Every other command prints
# below the last exchange, so dispatch invalidates the ledger before it.
_PLAYING = {"/me", "/you", "/ooc", "/undo", "/regen"}


def dispatch(line: str, session: Session, store: Store) -> bool:
    """True when the line was a slash command (handled); False when it
    should go to the model as a user message. The line lands verbatim in
    `session.raw_line` (a playing command echoes exactly what was typed)
    and its argument text in `session.raw_args` (free-text handlers keep
    the user's exact spacing — split-and-rejoin would collapse it)."""
    if not line.startswith("/"):
        return False
    command, *args = line.split()
    session.raw_line = line
    split_once = line.split(None, 1)
    session.raw_args = split_once[1] if len(split_once) > 1 else ""
    entry = COMMANDS.get(command)
    with session.screen.command_output():
        if entry is None:
            session.screen.invalidate()
            print(f"Unknown command: {command}. Type /help.")
            return True
        if command not in _PLAYING:
            session.screen.invalidate()
        handler, _ = entry
        handler(session, store, args)
        return True
