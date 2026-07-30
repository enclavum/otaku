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
# here" (Tab-triggered; see completer.py). Paths may contain spaces —
# handlers read them from raw_args, never from split args.
PATH_LEAF = "<path>"

CompletionTree = dict[str, "CompletionTree | str | None"]

CommandHandler = Callable[[Session, Store, list[str]], None]


# (command, shortcut, description) per row; "" for no shortcut, None starts
# a group heading. A description is ONE line in the printed help, whatever
# its length — split literals below wrap the source, never the output.
_HELP_ROWS: list[tuple[str | None, str, str]] = [
    (None, "", "Playing:"),
    (
        "PROMPT",
        "",
        "Your character speaks or acts; the model continues the scene — sent verbatim",
    ),
    ("/me NAME: PROMPT", "", "Send PROMPT as NAME's line; you keep writing as NAME"),
    ("/you NAME", "", "The model plays NAME and responds"),
    ("/ooc PROMPT", "", "Talk to the model out of character"),
    ("/undo", "Ctrl+U", "Discard the last prompt and response"),
    ("/regen", "Ctrl+R", "Re-run the last prompt (mid-stream: cancel + regen)"),
    (None, "", "Stories:"),
    ("/stories", "Ctrl+T", "Browse stories, resume an old one"),
    ("/fork [TITLE]", "", "Continue in a copy of this story; the original stays"),
    ("/system <text>", "", "Set the system prompt (for this story)"),
    ("/rename NEW", "", "Title this story (shown in /stories)"),
    ("/new", "", "Clear context and start a new story"),
    (None, "", "Lore:"),
    ("/lore", "Ctrl+L", "Browse and edit the memory: scenes, cast, journals"),
    ("/cast", "", "The same browser, opened directly on the cast"),
    (
        "/extract",
        "",
        "Extract lore from the recent messages now (closes a scene); "
        "triggered automatically after 5 minutes of inactivity",
    ),
    ("/merge A into B", "", "Fold a duplicate character into the real one"),
    (None, "", "Inspect:"),
    ("/context", "", "Preview the next request (assembled prompt + budgets)"),
    ("/usage [all]", "", "Tokens spent on this story (or everything)"),
    ("/info", "", "Show details about the current model + session"),
    (None, "", "Import/export:"),
    ("/import chat FILE", "", "Import a chat: an otaku /export file, or SillyTavern .jsonl"),
    ("/import text FILE", "", "Build a new story from a free text file (current model)"),
    ("/export [FILE]", "", "Export the whole story to Markdown (memory + messages)"),
    ("/copy [all]", "", "Copy the last reply (or the whole chat) to the clipboard"),
    (None, "", "Model and settings:"),
    (
        "/model [PROVIDER/MODEL]",
        "Ctrl+O",
        "Switch model in-place (opens the picker with no arg); remembered as last used",
    ),
    (
        "/set think <level>",
        "",
        "Thinking effort for the model: on|off|none|low|medium|high|max|default",
    ),
    (
        "/set parameter <name> <val>",
        "",
        "Set an inference parameter for the model; "
        "no <val> shows it, <val> = reset returns the default",
    ),
    ("/set verbose on|off", "", "Show the stats line after each reply"),
    ("", "", ""),
    ("/bye", "Ctrl+D", "Exit"),
    ("/help", "", "Show this help"),
    (None, "", "Keys at the prompt:"),
    ('"""', "", 'Begin a multiline message; close it with """'),
    ("/", "", "Open the command menu; it filters as you type"),
    ("Tab", "", "Cycle through the menu's completions"),
    ("Up / Down", "", "Walk your recent prompt history (saved across runs)"),
    ("Ctrl+C", "", "Clear the current line; cancel an in-flight reply"),
]


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


# Each entry pairs the handler with its tab-completion subtree (None = no
# arguments). Ordered as in _HELP_TEXT so the menu lists commands in the
# same order as the help.
COMMANDS: dict[str, tuple[CommandHandler, CompletionTree | str | None]] = {
    "/me": (playing.cmd_me, None),
    "/you": (playing.cmd_you, None),
    "/ooc": (playing.cmd_ooc, None),
    "/undo": (playing.cmd_undo, None),
    "/regen": (playing.cmd_regen, None),
    "/stories": (stories.cmd_stories, None),
    "/fork": (stories.cmd_fork, None),
    "/system": (stories.cmd_system, None),
    "/rename": (stories.cmd_rename, None),
    "/new": (stories.cmd_new, None),
    "/lore": (lore.cmd_lore, None),
    "/cast": (lore.cmd_cast, None),
    "/extract": (lore.cmd_extract, None),
    "/merge": (lore.cmd_merge, None),
    "/context": (inspect.cmd_context, None),
    "/usage": (inspect.cmd_usage, {"all": None}),
    "/info": (inspect.cmd_info, None),
    "/import": (transfer.cmd_import, {"chat": PATH_LEAF, "text": PATH_LEAF}),
    "/export": (transfer.cmd_export, PATH_LEAF),
    "/copy": (transfer.cmd_copy, {"all": None}),
    "/model": (settings.cmd_model, None),
    "/set": (
        settings.cmd_set,
        {
            "think": {
                k: None for k in ("on", "off", "none", "low", "medium", "high", "max", "default")
            },
            "parameter": {p: {"reset": None} for p in KNOWN_PARAMS},
            "verbose": {"on": None, "off": None},
        },
    ),
    "/bye": (meta.cmd_bye, None),
    "/help": (meta.cmd_help, None),
}


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


def dispatch(line: str, session: Session, store: Store) -> bool:
    """True when the line was a slash command (handled); False when it
    should go to the model as a user message. Alongside the split args, the
    verbatim argument text lands in `session.raw_args` for handlers that
    take free text — split-and-rejoin would collapse the user's spacing."""
    if not line.startswith("/"):
        return False
    command, *args = line.split()
    entry = COMMANDS.get(command)
    if entry is None:
        print(f"Unknown command: {command}. Type /help.")
        return True
    split_once = line.split(None, 1)
    session.raw_args = split_once[1] if len(split_once) > 1 else ""
    handler, _ = entry
    handler(session, store, args)
    return True
