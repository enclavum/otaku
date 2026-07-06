"""Slash commands and session settings.

The `/…` command handlers and the `COMMANDS` dispatch/completion table. The
session-state primitives they build on — `State`, `persist`, `run_inference`,
`run_oneshot` — live in `chat/inference.py`; this module is the command layer
on top of them.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from otaku.chat import clipboard
from otaku.chat.inference import DIM, RESET, State, _has_real_turn, persist, run_inference
from otaku.chat.mdstream import MarkdownStreamer
from otaku.client import client_for
from otaku.config import Settings, remember_model_settings
from otaku.pickers.history import pick_history
from otaku.pickers.model import pick_model
from otaku.storage.store import Message, Store
from otaku.text import flatten, format_size, truncate

KNOWN_PARAMS: dict[str, type] = {
    "temperature": float,
    "top_p": float,
    "max_tokens": int,
    "presence_penalty": float,
    "frequency_penalty": float,
    "seed": int,
    "stop": str,
}


def _print_messages(messages: list[Message]) -> None:
    """Print `[role]` header + content for each message, blank line
    between. Shared by `/print` and the post-/history preview. Content
    is routed through MarkdownStreamer so inline emphasis / code styles
    render the same way they do during live streaming."""
    for i, m in enumerate(messages):
        if i > 0:
            print()
        print(f"{DIM}[{m.role}]{RESET}")
        md = MarkdownStreamer()
        md.feed(m.content)
        md.flush()
        print()  # `print` adds the trailing newline that md.feed doesn't


def apply_settings(state: State, s: Settings) -> None:
    """Seed a fresh session's State from persisted defaults (system prompt,
    think effort, parameters). Called once at launch, before any `/set`; an
    in-session `/set` then overrides these in memory only."""
    if s.system:
        state.messages.insert(0, Message(role="system", content=s.system))
    if s.think is not None:
        if s.think == "default":
            state.think = None
        elif s.think in _THINK_LEVELS:
            state.think = s.think
        else:
            print(f"Ignoring unknown think value {s.think!r} in defaults.", file=sys.stderr)
    for name, value in s.parameters.items():
        coerce = KNOWN_PARAMS.get(name)
        if coerce is None:
            print(f"Ignoring unknown parameter {name!r} in defaults.", file=sys.stderr)
            continue
        try:
            state.params[name] = coerce(value)
        except (ValueError, TypeError):
            print(f"Ignoring invalid {name} value {value!r} in defaults.", file=sys.stderr)


# --- slash commands ---


def _system_message(state: State) -> Message | None:
    """The leading system message, if the conversation has one."""
    if state.messages and state.messages[0].role == "system":
        return state.messages[0]
    return None


def _keep_system(state: State) -> None:
    """Reset messages to just the system prompt (if any)."""
    sys_msg = _system_message(state)
    state.messages = [sys_msg] if sys_msg else []


def cmd_clear(state: State, store: Store, args: list[str]) -> None:
    """Clear the in-memory context (keeping any system prompt) but stay in the
    current conversation — `conv_id` is unchanged, so continuing re-snapshots
    this same conversation. Use /new to start a separate one instead."""
    _keep_system(state)
    # conv_id kept; the DB row is left untouched until the next turn re-snapshots.
    print("Cleared context (still in this conversation).")


def cmd_new(state: State, store: Store, args: list[str]) -> None:
    """Start a brand-new conversation: clear the context and detach from the
    current conversation row (which stays intact in history). The next user
    turn creates a fresh row."""
    _keep_system(state)
    state.conv_id = None
    print("Started a new conversation.")


def cmd_undo(state: State, store: Store, args: list[str]) -> None:
    # Pop the trailing turn: a user-assistant pair, or a lone trailing user
    # message left behind by an interrupted reply.
    if not state.messages or state.messages[-1].role == "system":
        print("Nothing to undo.")
        return
    if state.messages[-1].role == "assistant":
        if len(state.messages) < 2 or state.messages[-2].role != "user":
            print("Nothing to undo.")
            return
        state.messages.pop()  # assistant
        state.messages.pop()  # the user prompt that produced it
    else:
        state.messages.pop()  # orphan user message from an interrupted reply
    if state.conv_id is not None:
        if _has_real_turn(state.messages):
            store.snapshot_messages(state.conv_id, state.messages)
        else:
            # Undid the only turn — drop the now-empty row entirely.
            store.delete_conversation(state.conv_id)
            state.conv_id = None
    if not _has_real_turn(state.messages):
        print("Undone. Conversation is now empty.")
        return
    print("Undone. Conversation now ends with:")
    tail = [m for m in state.messages if m.role != "system"][-2:]
    width = shutil.get_terminal_size((100, 24)).columns
    for m in tail:
        prefix = f"[{m.role}] "
        avail = max(20, width - len(prefix))
        print(prefix + truncate(flatten(m.content), avail))


def cmd_regenerate(state: State, store: Store, args: list[str]) -> None:
    if state.messages and state.messages[-1].role == "assistant":
        state.messages.pop()
        persist(state, store)
        run_inference(state, store)
    else:
        print("Nothing to regenerate.")


def cmd_history(state: State, store: Store, args: list[str]) -> None:
    if not store.list_conversations(limit=1):
        print("No saved conversations yet.")
        return
    result = pick_history(store, initial_id=state.conv_id)
    if result is None:
        return
    chosen, truncated, total = result

    # Picking an earlier turn (not the last) would TRUNCATE the saved
    # conversation on the next persist — destructive. Offer to fork
    # instead, which leaves the original intact. Skip the prompt in
    # read-only sessions (where neither path writes anything anyway).
    if len(truncated) < total and not store.read_only:
        discarded = total - len(truncated)
        try:
            ans = (
                input(
                    f"Resuming at turn {len(truncated)} of {total} would discard "
                    f"{discarded} later message(s) on next save. Fork into a new "
                    f"conversation? [Y/n] "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("Cancelled.")
            return
        if ans in ("", "y", "yes"):
            new_id = store.create_conversation(state.full_model)
            store.snapshot_messages(new_id, truncated)
            chosen = new_id
            print("Forked into a new conversation.")
        elif ans in ("n", "no"):
            print("Resuming destructively — later messages will be lost on next save.")
        else:
            print("Cancelled.")
            return

    # The conversation we're leaving is summarized opportunistically by the
    # background SummaryWorker (during idle) — never synchronously here, so
    # switching conversations stays instant.
    state.conv_id = chosen
    state.messages = truncated
    print(f"Resumed conversation at message {len(truncated)}.")
    print()
    _print_messages(state.messages[-3:])


def cmd_fork(state: State, store: Store, args: list[str]) -> None:
    if not _has_real_turn(state.messages):
        print("Nothing to fork: current conversation is empty.")
        return
    new_id = store.create_conversation(state.full_model)
    store.snapshot_messages(new_id, state.messages)
    state.conv_id = new_id
    print("Forked into new conversation.")


def _switch_model(state: State, provider_name: str, model: str) -> None:
    if provider_name not in state.config.providers:
        print(f"Unknown provider {provider_name!r}.")
        return
    if f"{provider_name}/{model}" == state.full_model:
        print(f"Already using {state.full_model}.")
        return
    state.provider = state.config.providers[provider_name]
    state.model = model
    state.full_model = f"{provider_name}/{model}"
    print(f"Switched to {state.full_model}.")


def cmd_model(state: State, store: Store, args: list[str]) -> None:
    """Switch the model for the rest of this session, keeping the conversation
    context (handy for comparing models on the same prompt — switch, then
    /regenerate). `/model` opens the picker; `/model PROVIDER/MODEL` switches
    directly."""
    providers = state.config.providers
    if args:
        head, _, rest = " ".join(args).partition("/")
        if head in providers and rest:
            _switch_model(state, head, rest)
        else:
            known = ", ".join(sorted(providers))
            print(f"Use PROVIDER/MODEL (providers: {known}), or /model with no args to pick.")
        return
    spec = pick_model(providers, initial_spec=state.full_model)
    if spec is None:
        return  # cancelled — keep the current model
    head, _, rest = spec.partition("/")
    _switch_model(state, head, rest)


def cmd_bye(state: State, store: Store, args: list[str]) -> None:
    state.quit = True


def cmd_help(state: State, store: Store, args: list[str]) -> None:
    print(HELP_TEXT)


def cmd_print(state: State, store: Store, args: list[str]) -> None:
    """Dump the in-memory message list — the same content the model sees
    on the next turn. Useful for verifying what /undo / /clear /
    /history actually did."""
    if not state.messages:
        print("(empty conversation)")
        return
    _print_messages(state.messages)


def _last_assistant(messages: list[Message]) -> str | None:
    for m in reversed(messages):
        if m.role == "assistant":
            return m.content
    return None


def _transcript_markdown(state: State) -> str:
    """Render the conversation as a Markdown transcript — a header line plus a
    `## <role>` section per message with the content verbatim (code fences
    intact). Shared by /copy all and /save."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# {state.full_model} · {stamp}", ""]
    for m in state.messages:
        lines.append(f"## {m.role}")
        lines.append("")
        lines.append(m.content)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def cmd_copy(state: State, store: Store, args: list[str]) -> None:
    """Copy the last assistant reply to the clipboard, or the whole conversation
    (as Markdown) with `/copy all`. Uses a native clipboard tool when available,
    else the OSC 52 terminal escape."""
    if args and args[0].lower() != "all":
        print("Usage: /copy [all]")
        return
    if not _has_real_turn(state.messages):
        print("Nothing to copy.")
        return
    if args:  # /copy all
        text, desc = _transcript_markdown(state), "conversation"
    else:
        reply = _last_assistant(state.messages)
        if not reply:
            print("Nothing to copy (no assistant reply yet).")
            return
        text, desc = reply, "last reply"
    method = clipboard.copy(text)
    suffix = " (via OSC 52)" if method == "osc52" else ""
    print(f"Copied {desc} to clipboard ({len(text):,} chars){suffix}.")


def cmd_save(state: State, store: Store, args: list[str]) -> None:
    """Save the whole conversation as a Markdown file. `/save <file>`. Refuses to
    overwrite an existing file. Works even in a no-record (`-nr`) session — an
    explicit export is the escape hatch to keep something from it."""
    if not args:
        print("Usage: /save <file>")
        return
    if not _has_real_turn(state.messages):
        print("Nothing to save yet.")
        return
    path = Path(" ".join(args)).expanduser()
    if path.exists():
        print(f"{path} already exists — choose another name.")
        return
    try:
        path.write_text(_transcript_markdown(state), encoding="utf-8")
    except OSError as e:
        print(f"Could not write {path}: {e}")
        return
    print(f"Saved conversation to {path} ({len(state.messages)} messages).")


def cmd_remember(state: State, store: Store, args: list[str]) -> None:
    """Persist the current system prompt, think effort, and parameters as the
    defaults for this model (written to ~/.otaku/model_defaults.json, keyed by
    the bare model name). Applied automatically on the next launch of this
    model, under the global [defaults]."""
    sys_msg = _system_message(state)
    think = "default" if state.think is None else state.think
    settings = Settings(
        system=sys_msg.content if sys_msg else None,
        think=think,
        parameters=dict(state.params),
    )
    try:
        remember_model_settings(state.model, settings)
    except OSError as e:
        print(f"Could not save defaults: {e}")
        return
    bits = [f"think={think}"]
    if sys_msg:
        bits.append("system")
    if state.params:
        bits.append(f"{len(state.params)} param(s)")
    print(f"Remembered defaults for {state.model} ({', '.join(bits)}).")


def cmd_title(state: State, store: Store, args: list[str]) -> None:
    """Set a title for the current conversation, shown in the /history picker
    (independent of the auto-generated summary). `/title <text>`."""
    title = " ".join(args).strip()
    if not title:
        print("Usage: /title <text>")
        return
    persist(state, store)  # lazily create the conversation row if there's a real turn
    if state.conv_id is None:
        print("Nothing to title yet — send a message first.")
        return
    store.update_title(state.conv_id, title)
    print(f'Title set: "{title}".')


def cmd_info(state: State, store: Store, args: list[str]) -> None:
    """Best-effort dump of everything otaku knows about the active model
    and conversation. Network-backed fields (loaded state, on-disk size)
    are silently skipped if the provider doesn't expose them or the
    request fails."""
    cli = client_for(state.provider)
    p = state.provider

    print(f"Model:    {state.full_model}")
    print(f"Backend:  {cli.kind} ({p.url})")
    if p.api_key:
        print("Auth:     api_key configured")

    # Load state — only meaningful for backends that expose it.
    if cli.kind != "openai":
        try:
            loaded = state.model in cli.loaded_models()
        except Exception:
            loaded = None
        if loaded is not None:
            print(f"Loaded:   {'yes' if loaded else 'no'}")

    ctx = cli.context_size(state.model)
    if ctx is not None:
        print(f"Context:  {ctx}")

    try:
        size = cli.model_sizes().get(state.model)
    except Exception:
        size = None
    if size is not None and size > 0:
        print(f"Size:     {format_size(size)}")

    if p.supports_thinking:
        think_state = state.think if state.think else "default"
        print(f"Thinking: supported, currently {think_state}")
    else:
        print("Thinking: not supported")

    if cli.kind == "ollama":
        print(f"Keep-alive: {p.keep_alive}")

    print()

    msg_count = len(state.messages)
    user_count = sum(1 for m in state.messages if m.role == "user")
    assistant_count = sum(1 for m in state.messages if m.role == "assistant")
    print(f"Conversation: {msg_count} messages ({user_count} user, {assistant_count} assistant)")
    if state.conv_id is not None:
        print(f"  id: {state.conv_id}")

    sys_msg = _system_message(state)
    if sys_msg is not None:
        print(f'System: "{sys_msg.content}"')

    if state.params:
        params_str = ", ".join(f"{k} = {v}" for k, v in state.params.items())
        print(f"Parameters: {params_str}")


def cmd_set(state: State, store: Store, args: list[str]) -> None:
    if not args:
        print(
            "Usage: /set system <text> | /set think on|off | /set verbose on|off "
            "| /set parameter <name> [value]"
        )
        return
    sub, *rest = args
    if sub == "system":
        _set_system(state, store, rest)
    elif sub == "think":
        _set_think(state, rest)
    elif sub == "verbose":
        _set_verbose(state, rest)
    elif sub == "parameter":
        _set_parameter(state, rest)
    else:
        print(f"Unknown subcommand: /set {sub}")


def _set_verbose(state: State, rest: list[str]) -> None:
    if not rest:
        print(f"Verbose: {'on' if state.verbose else 'off'}.")
        return
    val = rest[0].lower()
    if val in ("on", "true", "yes"):
        state.verbose = True
    elif val in ("off", "false", "no"):
        state.verbose = False
    else:
        print("Usage: /set verbose on|off")
        return
    print(f"Verbose: {'on' if state.verbose else 'off'}.")


def _set_system(state: State, store: Store, rest: list[str]) -> None:
    text = " ".join(rest).strip()
    if not text:
        sys_msg = _system_message(state)
        print(f'System: "{sys_msg.content}"' if sys_msg else "System: (none)")
        return
    if _system_message(state) is not None:
        state.messages[0] = Message(role="system", content=text)
    else:
        state.messages.insert(0, Message(role="system", content=text))
    persist(state, store)
    print(f"System prompt set ({len(text)} chars).")


_THINK_ALIASES = {"on": "medium", "off": "none"}
_THINK_LEVELS = {"none", "low", "medium", "high", "max"}


def _set_think(state: State, rest: list[str]) -> None:
    if not rest:
        current = state.think if state.think else "default"
        print(f"Think: {current}.")
        return
    val = rest[0].lower()
    val = _THINK_ALIASES.get(val, val)
    if val == "default":
        state.think = None
        print("Think: default (no reasoning_effort sent).")
        return
    if val not in _THINK_LEVELS:
        print("Usage: /set think on|off|none|low|medium|high|max|default")
        return
    if val != "none" and not state.provider.supports_thinking:
        print(f"Thinking is not supported by provider {state.provider.name!r}.")
        return
    state.think = val
    print(f"Think: {val}.")


def _set_parameter(state: State, rest: list[str]) -> None:
    if not rest:
        if not state.params:
            print("No parameters set.")
            return
        print("Parameters:")
        for k, v in state.params.items():
            print(f"  {k} = {v}.")
        return
    name = rest[0]
    if name not in KNOWN_PARAMS:
        known = ", ".join(KNOWN_PARAMS)
        print(f"Unknown parameter {name!r}. Known: {known}.")
        return
    if len(rest) == 1:
        if name in state.params:
            state.params.pop(name)
            print(f"Parameter {name} cleared.")
        else:
            print(f"Parameter {name} is not set.")
        return
    raw = " ".join(rest[1:])
    coerce = KNOWN_PARAMS[name]
    try:
        value = coerce(raw)
    except ValueError:
        print(f"Could not parse {raw!r} as {coerce.__name__}.")
        return
    state.params[name] = value
    print(f"{name} = {value}.")


HELP_TEXT = """\
Commands:
  /clear                       Clear context, stay in the same conversation
  /new                         Clear context and start a new conversation
  /model [PROVIDER/MODEL]      Switch model in-place (opens the picker with no arg)
  /undo                        Discard the last prompt and response (Ctrl+U)
  /regenerate                  Re-run the last prompt (Ctrl+R)
  /history                     Browse saved conversations and resume any turn (Ctrl+T)
  /fork                        Snapshot the current conversation as a new branch
  /info                        Show details about the current model + session
  /print                       Dump the full message history (what the model sees)
  /copy [all]                  Copy the last reply (or whole chat) to the clipboard
  /save <file>                 Save the conversation to a Markdown file
  /title <text>                Name this conversation (shown in /history)
  /remember                    Save current system/think/params as this model's defaults
  /set system <text>           Set the system prompt
  /set think <level>           Set thinking effort (on|off|none|low|medium|high|max|default)
  /set verbose on|off          Show the stats line after each reply (off by default)
  /set parameter <name> [val]  Set or clear an inference parameter
  /bye                         Exit (Ctrl+D)
  /?, /help                    Show this help

Keys at the prompt:
  \"\"\"                          Begin a multiline message; close it with \"\"\"
  Tab                          Complete slash commands
  Up / Down                    Walk this session's prompt history
  Ctrl+R                       /regenerate the last response
                               (during streaming: cancel + regenerate)
  Ctrl+U                       /undo the last turn
  Ctrl+T                       Open /history picker
  Ctrl+D                       /bye (also exits on empty line)
  Ctrl+C                       Clear the current line; cancel an in-flight reply
"""


CompletionTree = dict[str, "CompletionTree | None"]

CommandHandler = Callable[[State, Store, list[str]], None]

# Each entry pairs the handler with its tab-completion subtree (or None
# when the command takes no arguments). Adding a command means a single
# row here — completer.py reads the subtrees via `completion_tree()`.
COMMANDS: dict[str, tuple[CommandHandler, CompletionTree | None]] = {
    "/clear": (cmd_clear, None),
    "/new": (cmd_new, None),
    "/model": (cmd_model, None),
    "/regenerate": (cmd_regenerate, None),
    "/undo": (cmd_undo, None),
    "/history": (cmd_history, None),
    "/fork": (cmd_fork, None),
    "/bye": (cmd_bye, None),
    "/help": (cmd_help, None),
    "/?": (cmd_help, None),
    "/info": (cmd_info, None),
    "/print": (cmd_print, None),
    "/copy": (cmd_copy, {"all": None}),
    "/save": (cmd_save, None),
    "/title": (cmd_title, None),
    "/remember": (cmd_remember, None),
    "/set": (
        cmd_set,
        {
            "system": None,
            "think": {
                k: None for k in ("on", "off", "none", "low", "medium", "high", "max", "default")
            },
            "verbose": {"on": None, "off": None},
            "parameter": {p: None for p in KNOWN_PARAMS},
        },
    ),
}


def completion_tree() -> CompletionTree:
    """Slash-completion tree derived from COMMANDS — keeps the completer
    in lockstep with the dispatch table."""
    return {name: subtree for name, (_, subtree) in COMMANDS.items()}


def dispatch(line: str, state: State, store: Store) -> bool:
    """Returns True if the line was a slash command (handled), False if it
    should be sent to the model as a user message."""
    if not line.startswith("/"):
        return False
    parts = line.split()
    if not parts:
        return True
    cmd, *args = parts
    entry = COMMANDS.get(cmd)
    if entry is None:
        print(f"Unknown command: {cmd}. Type /help.")
        return True
    handler, _ = entry
    handler(state, store, args)
    return True
