"""Interactive REPL: prompt_toolkit input + streaming model output.

Keybindings:
  Ctrl+R -> /regenerate
  Ctrl+U -> /undo
  Ctrl+T -> /history    (Ctrl+H is reserved for backspace by the terminal)
  Ctrl+D -> /bye

Multiline input follows Ollama's convention: a line starting with `\"\"\"`
opens a block that spans lines until a closing `\"\"\"` ends one; the text
between the delimiters (newlines preserved) is sent as a single message.
`LineAssembler` implements the state machine.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from otaku.chat.commands import dispatch
from otaku.chat.completer import build_completer
from otaku.chat.inference import State, persist, run_inference
from otaku.chat.summary import SummaryWorker
from otaku.storage.store import Message, Store

_PLACEHOLDER = FormattedText([("class:placeholder", "Send a message (/help for commands)")])
_PROMPT_STYLE = Style.from_dict({"placeholder": "fg:#8a8a8a"})

_TRIPLE = '"""'


def _cut_suffix(s: str, suffix: str) -> tuple[str, bool]:
    """Return (s without a trailing `suffix`, True) when present, else (s, False)."""
    if s.endswith(suffix):
        return s[: -len(suffix)], True
    return s, False


class LineAssembler:
    """Assembles Ollama-style triple-quoted multiline input, one line at a time.

    Feed each raw input line via `feed()`. It returns `None` while a `\"\"\"`
    block is still open (the caller should keep prompting with the continuation
    prompt), otherwise `(text, is_raw)`:

    - `is_raw=True` when the text came from a `\"\"\"` wrapper (single- or
      multi-line). Such text is a literal user message: the caller must skip
      command dispatch and the usual whitespace strip.
    - `is_raw=False` for an ordinary line; `text` is the line verbatim.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self.in_block = False

    def feed(self, line: str) -> tuple[str, bool] | None:
        if self.in_block:
            before, closed = _cut_suffix(line, _TRIPLE)
            self._lines.append(before)
            if not closed:
                return None  # closing delimiter not seen yet — keep collecting
            text = "\n".join(self._lines)
            self.reset()
            return text, True
        if line.startswith(_TRIPLE):
            rest, closed = _cut_suffix(line[len(_TRIPLE) :], _TRIPLE)
            if closed:
                return rest, True  # single-line """text"""
            self._lines = [rest]
            self.in_block = True
            return None
        return line, False

    def reset(self) -> None:
        """Drop any partial block (used on Ctrl+C)."""
        self._lines = []
        self.in_block = False


def run(state: State, store: Store, summary: SummaryWorker | None = None) -> None:
    # CSI ?12 h opts into cursor blinking — some terminals (Ghostty)
    # require this on top of the DECSCUSR shape escape that
    # prompt_toolkit emits for CursorShape.BLINKING_*.
    sys.stdout.write("\x1b[?12h")
    sys.stdout.flush()

    record_tag = " (not recorded)" if store.read_only else ""
    print(f"Connected to {state.full_model}{record_tag} — type /help for commands.")

    # A shortcut key (Ctrl+T etc.) stashes the in-progress input here before
    # exiting the prompt with its command, so the next prompt restores it — e.g.
    # cancelling the /history picker returns you to your line, not a blank one.
    carry: dict[str, str] = {}
    bindings = _make_bindings(carry)
    completer = build_completer()
    session: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        key_bindings=bindings,
        complete_while_typing=False,
        enable_history_search=True,
        style=_PROMPT_STYLE,
        cursor=CursorShape.BLINKING_BEAM,
    )

    if summary is not None:
        summary.start()

    assembler = LineAssembler()
    while not state.quit:
        default = carry.pop("text", "")
        try:
            if assembler.in_block:
                line = session.prompt("... ", placeholder=None, default=default)
            else:
                line = session.prompt(">>> ", placeholder=_PLACEHOLDER, default=default)
        except EOFError:
            state.quit = True
            break
        except KeyboardInterrupt:
            # ^C clears the line; inside a """ block it also drops the buffer.
            assembler.reset()
            continue

        result = assembler.feed(line)
        if result is None:
            continue  # inside an open """ block — keep collecting lines

        text, is_raw = result
        message = text if is_raw else text.strip()
        if not message:
            continue

        # The user is active again: abort any pending/in-flight background
        # summary so their prompt never queues behind it (and /bye is instant).
        if summary is not None:
            summary.cancel()

        try:
            # A """-wrapped message is always a literal prompt, never a command.
            if not is_raw and dispatch(message, state, store):
                continue
            state.messages.append(Message(role="user", content=message))
            persist(state, store)  # save the user turn before talking to the model
            run_inference(state, store)
            # Arm an idle-debounced summary of the just-finished turn; it fires
            # while the user reads/thinks and is cancelled the moment they type.
            if summary is not None and state.conv_id is not None:
                summary.schedule(state.provider, state.model, state.conv_id, state.messages)
        except KeyboardInterrupt:
            # ^C during streaming or a picker: return to the prompt cleanly.
            print()

    if summary is not None:
        summary.shutdown()  # non-blocking: exit is immediate


_SHORTCUTS = {
    "c-r": "/regenerate",
    "c-u": "/undo",
    "c-t": "/history",
    "c-d": "/bye",
}


def _make_bindings(carry: dict[str, str]) -> KeyBindings:
    kb = KeyBindings()
    for key, command in _SHORTCUTS.items():
        kb.add(key)(_submit(command, carry))
    return kb


def _submit(command: str, carry: dict[str, str]) -> Callable[[Any], None]:
    """Exit the prompt directly with `command` as the result — without
    rendering it into the buffer (no visible flash) or running it through
    `validate_and_handle` (no history append)."""

    def handler(event: Any) -> None:
        carry["text"] = event.current_buffer.text  # restore the line after the command
        event.app.exit(result=command)

    return handler
