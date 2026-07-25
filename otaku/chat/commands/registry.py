"""Slash commands: the dispatch table, the completion tree, and /help.

`COMMANDS` pairs each command with its handler and its tab-completion
subtree; the completer derives its tree from it and the descriptions from
`_HELP_ROWS`, so the menu, the completions, and /help can never disagree.
"""

from collections.abc import Callable

from otaku.chat.commands import inspect, playing, settings, stories
from otaku.chat.state import KNOWN_PARAMS, Session
from otaku.store import Store

# A completion-tree leaf may be PATH_LEAF: "complete a filesystem path
# here" (Tab-triggered; see completer.py). Paths may contain spaces —
# handlers read them from raw_args, never from split args.
PATH_LEAF = "<path>"

CompletionTree = dict[str, "CompletionTree | str | None"]

CommandHandler = Callable[[Session, Store, list[str]], None]


# (command, shortcut, description) per row; "" for no shortcut, None starts
# a group heading. A trailing tuple element is a wrapped continuation line.
_HELP_ROWS: list[tuple[str | None, str, str] | tuple[str | None, str, str, str]] = [
    (None, "", "Playing:"),
    (
        "PROMPT",
        "",
        "Your character speaks or acts; the model continues the",
        "scene. What you type is sent verbatim",
    ),
    ("/me NAME - PROMPT", "", "Send PROMPT as NAME's line; you keep writing as NAME"),
    ("/you NAME", "", "The model plays NAME and responds"),
    ("/ooc <text>", "", "Talk to the model out of character"),
    ("/undo", "Ctrl+U", "Discard the last prompt and response"),
    ("/regen", "Ctrl+R", "Re-run the last prompt (mid-stream: cancel + regen)"),
    (None, "", "Stories:"),
    ("/system <text>", "", "Set the system prompt (for this story)"),
    ("/new", "", "Clear context and start a new story"),
    (None, "", "Inspect:"),
    ("/context", "", "Preview the next request (assembled prompt + budgets)"),
    ("/info", "", "Show details about the current model + session"),
    (None, "", "Model and settings:"),
    (
        "/model [PROVIDER/MODEL]",
        "Ctrl+O",
        "Switch model in-place (opens the picker with no arg);",
        "remembered as last used",
    ),
    (
        "/set think <level>",
        "",
        "Thinking effort: on|off|none|low|medium|high|max|default",
        "(for the model)",
    ),
    (
        "/set parameter <name> <val>",
        "",
        "Set an inference parameter for the model;",
        "<val> = reset returns it to the default",
    ),
    ("/set verbose on|off", "", "Show the stats line after each reply"),
    ("", "", ""),
    ("/bye", "Ctrl+D", "Exit"),
    ("/?, /help", "", "Show this help"),
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
    desc_col = 2 + cmd_width + 1 + key_width + 2
    lines: list[str] = []
    for row in _HELP_ROWS:
        command, key, desc, *cont = row
        if command is None:  # a group heading — one blank line before it
            if lines:
                lines.append("")
            lines.append(desc)
            continue
        lines.append(f"  {command:<{cmd_width}} {key:<{key_width}}  {desc}".rstrip())
        lines.extend(" " * desc_col + line for line in cont)
    return "\n".join(lines)


_HELP_TEXT = _build_help()


def cmd_bye(session: Session, store: Store, args: list[str]) -> None:
    session.should_quit = True


def cmd_help(session: Session, store: Store, args: list[str]) -> None:
    print(_HELP_TEXT)


# Each entry pairs the handler with its tab-completion subtree (None = no
# arguments). Ordered as in _HELP_TEXT so the menu lists commands in the
# same order as the help.
COMMANDS: dict[str, tuple[CommandHandler, CompletionTree | None]] = {
    "/me": (playing.cmd_me, None),
    "/you": (playing.cmd_you, None),
    "/ooc": (playing.cmd_ooc, None),
    "/undo": (playing.cmd_undo, None),
    "/regen": (playing.cmd_regen, None),
    "/system": (stories.cmd_system, None),
    "/new": (stories.cmd_new, None),
    "/context": (inspect.cmd_context, None),
    "/info": (inspect.cmd_info, None),
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
    "/bye": (cmd_bye, None),
    "/help": (cmd_help, None),
    "/?": (cmd_help, None),
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
