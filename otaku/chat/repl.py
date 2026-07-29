"""Interactive REPL: prompt_toolkit input + streaming model output.

Keybindings:
  Ctrl+R -> /regen
  Ctrl+U -> /undo
  Ctrl+T -> /stories
  Ctrl+O -> /model      (Ctrl+M is unusable — the terminal sends it as Enter)
  Ctrl+D -> /bye

Multiline input follows the `\"\"\"` convention: a line starting with `\"\"\"`
opens a block that spans lines until a closing `\"\"\"` ends one; the text
between the delimiters (newlines preserved) is sent as a single message.
`LineAssembler` implements the state machine.
"""

import contextlib
import sys
from collections.abc import Callable
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completion
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.filters import Condition, completion_is_selected, has_completions
from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from prompt_toolkit.history import History
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import menus as _ptk_menus
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.styles import Style

from otaku import __version__
from otaku.chat.commands.registry import dispatch
from otaku.chat.completer import SlashCompleter
from otaku.chat.inference import run_inference
from otaku.chat.state import Session
from otaku.formatting import flatten, truncate
from otaku.store import Store
from otaku.store.schema import Message
from otaku.term import banner

_PLACEHOLDER = FormattedText([("class:placeholder", "Send a message")])
_PROMPT_STYLE = Style.from_dict(
    {
        "placeholder": "fg:#8a8a8a",
        # The command menu: no colored panel — plain text on the terminal's
        # own background, dim descriptions, and the selected ROW (command and
        # description in ONE color) picked out by an accent instead of a
        # block. ANSI palette colors, so both dark and light themes work.
        "completion-menu": "bg:default",
        "completion-menu.completion": "bg:default fg:default",
        # `noreverse` matters: the default sheet marks the current row
        # `reverse`, and overriding only the colors leaves the flag on —
        # rendering as a colored BLOCK with swapped fg/bg.
        "completion-menu.completion.current": "bg:default fg:ansiblue noreverse",
        "completion-menu.meta.completion": "bg:default fg:ansibrightblack",
        "completion-menu.meta.completion.current": "bg:default fg:ansiblue noreverse",
        "scrollbar.background": "bg:default",
        "scrollbar.button": "bg:ansibrightblack",
    }
)

# Keys that submit a command instead of text.
_SHORTCUTS = {
    "c-r": "/regen",
    "c-u": "/undo",
    "c-t": "/stories",
    "c-o": "/model",  # Ctrl+M would have been the mnemonic, but that IS Enter
    "c-d": "/bye",
}

# One test for "is this a command line?", shared by the prompt's
# complete-while-typing filter and the menu bindings.
_SLASH_LINE = Condition(lambda: get_app().current_buffer.text.lstrip().startswith("/"))

_TRIPLE = '"""'

_RESUME_TURNS = 3  # turns shown under the banner when a story is resumed


class LineAssembler:
    """Assembles triple-quoted multiline input, one line at a time.

    Feed each raw input line via `feed()`. It returns None while a `\"\"\"`
    block is still open (the caller keeps prompting with the continuation
    prompt), otherwise `(text, is_raw)`: `is_raw=True` when the text came
    from a `\"\"\"` wrapper — a literal user message, so the caller must
    skip command dispatch and the usual whitespace strip."""

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
                return rest, True  # single-line \"\"\"text\"\"\"
            self._lines = [rest]
            self.in_block = True
            return None
        return line, False

    def reset(self) -> None:
        """Drop any partial block (used on Ctrl+C)."""
        self._lines = []
        self.in_block = False


class StoreHistory(History):
    """prompt_toolkit input history backed by the store: the last lines you
    submitted at the prompt, browsable with Up/Down across sessions.
    Best-effort — a store hiccup never breaks browsing."""

    def __init__(self, store: Store) -> None:
        super().__init__()
        self._store = store

    def load_history_strings(self) -> list[str]:
        try:
            return self._store.history.get_recent()  # already most-recent-first
        except Exception:
            return []

    def store_string(self, string: str) -> None:
        with contextlib.suppress(Exception):
            self._store.history.add(string)


def run(session: Session, store: Store) -> None:
    """The chat loop: banner, then prompt → command or model turn, until
    /bye, Ctrl+D, or EOF."""
    # CSI ?12 h opts into cursor blinking — some terminals (Ghostty) require
    # this on top of the DECSCUSR shape escape prompt_toolkit emits.
    sys.stdout.write("\x1b[?12h")
    sys.stdout.flush()

    if session.config.show_banner:
        print(_banner(session, store))
    _show_resumed(session, store)

    # A shortcut key stashes the in-progress input here before exiting the
    # prompt with its command, so the next prompt restores it.
    carry: dict[str, str] = {}
    prompt_session = _build_prompt(store, carry)
    assembler = LineAssembler()

    while not session.should_quit:
        default = carry.pop("text", "")
        try:
            if assembler.in_block:
                line = prompt_session.prompt("... ", placeholder=None, default=default)
            else:
                line = prompt_session.prompt(">>> ", placeholder=_PLACEHOLDER, default=default)
        except EOFError:
            session.should_quit = True
            break
        except KeyboardInterrupt:
            # ^C clears the line; inside a """ block it also drops the buffer.
            assembler.reset()
            continue

        # A shortcut key exits the prompt with its command as the result; it
        # is always a command — even mid-"""-block, where feeding it to the
        # assembler would paste "/regen" into the user's text.
        if carry.pop("shortcut", None) is not None:
            try:
                dispatch(line, session, store)
            except KeyboardInterrupt:
                print()
            continue

        result = assembler.feed(line)
        if result is None:
            continue  # inside an open """ block — keep collecting lines

        text, is_raw = result
        message = text if is_raw else text.strip()
        if not message:
            continue

        try:
            # A """-wrapped message is always a literal prompt, never a command.
            if not is_raw and dispatch(message, session, store):
                continue
            session.record_turn(store, Message(role="user", body=message))
            run_inference(session, store)
        except KeyboardInterrupt:
            # ^C during streaming or a picker: return to the prompt cleanly.
            print()


# ---------- session chrome ----------


def _banner(session: Session, store: Store) -> str:
    """The session header. Best-effort: a provider that can't report its
    context window just leaves that fact out — the banner never blocks or
    fails a launch."""
    client = session.providers.get_client(session.provider.name)
    try:
        context = client.get_context_size(session.model)
    except Exception:
        context = None
    story = flatten(truncate(session.story_label(store), 40))
    return banner.render(
        __version__,
        session.full_model_name,
        backend=client.kind,
        context=context,
        story=story,
    )


def _show_resumed(session: Session, store: Store) -> None:
    """A resumed story starts mid-scene: name what was resumed and show its
    last turns, so the scene is on screen before the prompt."""
    if not session.messages:
        return
    label = flatten(truncate(session.story_label(store), 40))
    if label:
        print(f"Story: {label}. Resumed at message {len(session.messages)}.")
    else:
        print(f"Resumed at message {len(session.messages)}.")
    print()
    print(session.render_last_turns(_RESUME_TURNS))


def _build_prompt(store: Store, carry: dict[str, str]) -> PromptSession[str]:
    """The prompt: store-backed history, the slash-command menu, and the
    keybindings.

    Typing `/` opens the menu with every command (+ its help line as the
    meta column) and each keystroke filters it. prompt_toolkit gates the
    menu as `complete_while_typing AND NOT enable_history_search`, so with
    history search OFF the menu still shows only on slash lines, and the
    completer also returns nothing on prose as a second guard.

    History search stays OFF on purpose: with it on, Up on a line you have
    started typing searches for entries with THAT prefix and finds none,
    freezing on your draft. Off, Up/Down are plain previous/next entry."""
    prompt_session: PromptSession[str] = PromptSession(
        history=StoreHistory(store),
        completer=SlashCompleter.build(),
        key_bindings=_make_bindings(carry),
        complete_while_typing=_SLASH_LINE,
        enable_history_search=False,
        style=_PROMPT_STYLE,
        cursor=CursorShape.BLINKING_BEAM,
    )
    # Snappy Esc for closing the command menu — the 0.5s default delay makes
    # the close feel broken.
    prompt_session.app.timeoutlen = 0.05
    prompt_session.app.ttimeoutlen = 0.05
    # Anchor the menu at the token being completed, not at the cursor. The
    # control isn't exposed as an attribute; at creation the layout's focus
    # is the default buffer window, whose content is that control.
    control = prompt_session.layout.current_control
    if isinstance(control, BufferControl):
        control.menu_position = lambda: _menu_anchor_index(
            prompt_session.default_buffer.document.text_before_cursor,
            prompt_session.default_buffer.cursor_position,
        )
    # Pre-select the first row whenever the menu (re)populates.
    prompt_session.default_buffer.on_completions_changed += lambda buf: _preselect_first(buf)
    return prompt_session


# ---------- keybindings ----------


def _make_bindings(carry: dict[str, str]) -> KeyBindings:
    kb = KeyBindings()
    for key, command in _SHORTCUTS.items():
        kb.add(key)(_submit(command, carry))

    # A pre-selected menu row is accepted by Enter (run it) or Tab (fill it
    # in and keep editing). The default bindings can't: they treat the
    # highlight as already-inserted text, but _preselect_first only sets the
    # index — the buffer still holds exactly what was typed.
    @kb.add("enter", filter=completion_is_selected)
    def _run_selected(event: Any) -> None:
        _accept_selection(event.current_buffer)
        event.current_buffer.validate_and_handle()

    @kb.add("tab", filter=completion_is_selected)
    def _fill_selected(event: Any) -> None:
        _accept_selection(event.current_buffer)

    # Up/Down navigate the menu when it is open, otherwise step through
    # input history — never walking the lines of a recalled multi-line
    # entry.
    @kb.add("up")
    def _up(event: Any) -> None:
        _menu_or_history_up(event.current_buffer, event.arg)

    @kb.add("down")
    def _down(event: Any) -> None:
        _menu_or_history_down(event.current_buffer, event.arg)

    # The command menu closes on Esc (the default Escape is a meta prefix
    # that does nothing visible here)…
    @kb.add("escape", filter=has_completions, eager=True)
    def _close_menu(event: Any) -> None:
        event.current_buffer.cancel_completion()

    # …and does NOT close on backspace. prompt_toolkit restarts the menu
    # only on text INSERTS, so a plain backspace while filtering would
    # dismiss it; this deletes and re-opens while the line is a command.
    @kb.add("backspace", filter=_SLASH_LINE)
    def _bs_refilter(event: Any) -> None:
        event.current_buffer.delete_before_cursor(count=event.arg)
        if event.current_buffer.text.lstrip().startswith("/"):
            event.current_buffer.start_completion(select_first=False)
        else:
            event.current_buffer.cancel_completion()

    return kb


def _submit(command: str, carry: dict[str, str]) -> Callable[[Any], None]:
    """Exit the prompt directly with `command` as the result — without
    rendering it into the buffer (no visible flash) or running it through
    `validate_and_handle` (no history append)."""

    def handler(event: Any) -> None:
        carry["text"] = event.current_buffer.text  # restore the line afterwards
        carry["shortcut"] = command  # the run loop must not feed this into a """ block
        event.app.exit(result=command)

    return handler


# ---------- the completion menu ----------


def _preselect_first(buff: Buffer) -> None:
    """Highlight the first row the moment the command menu opens, so a
    command is selected without an arrow press. Sets the index ONLY — the
    buffer text stays exactly as typed, so every keystroke keeps filtering
    the menu instead of the highlight jumping into the input line (which
    `go_to_completion` would do).

    Skips the lone exact-match completion (a fully-typed command):
    prompt_toolkit discards a single completion that inserts nothing, and
    nulls `complete_state` to do it — but only when no index is set;
    pinning one strands a state whose next Enter/Tab would read past the
    end. Leaving it unselected lets that cleanup run."""
    state = buff.complete_state
    if state is None or state.complete_index is not None or not state.completions:
        return
    if len(state.completions) == 1 and _completion_is_noop(state):
        return
    state.complete_index = 0


def _completion_is_noop(state: Any) -> bool:
    """True when the only completion just re-types what is already there —
    mirrors prompt_toolkit's own discard test."""
    before = state.original_document.text_before_cursor
    completion = state.completions[0]
    return bool(before[len(before) + completion.start_position :] == completion.text)


def _accept_selection(buff: Buffer) -> bool:
    """Apply the highlighted completion into the buffer (Tab/Enter accept a
    pre-selected row, which the default bindings can't — they assume the
    selection is already inserted). Guards the index against an empty list
    so a stray state can never raise."""
    state = buff.complete_state
    if state is None or state.complete_index is None or not state.completions:
        return False
    buff.apply_completion(state.completions[state.complete_index])
    return True


def _menu_or_history_up(buff: Buffer, count: int) -> None:
    """Up: navigate the menu when it is open, else the previous history
    entry — one ENTRY per press, never a line within a recalled multi-line
    entry (where `auto_up`'s cursor-row branch strands the key)."""
    if buff.complete_state:
        buff.complete_previous(count=count)
    else:
        buff.history_backward(count=count)


def _menu_or_history_down(buff: Buffer, count: int) -> None:
    if buff.complete_state:
        buff.complete_next(count=count)
    else:
        buff.history_forward(count=count)


def _menu_anchor_index(text_before_cursor: str, cursor: int) -> int | None:
    """Document index the completion menu anchors at: the start of the token
    being completed, so the menu opens right under the `/` (or under the
    cursor, where the next argument will be typed). None = default anchor."""
    if not text_before_cursor.lstrip().startswith("/"):
        return None
    if text_before_cursor.endswith((" ", "\t")):
        return cursor
    return cursor - len(text_before_cursor.split()[-1])


def _cut_suffix(text: str, suffix: str) -> tuple[str, bool]:
    if text.endswith(suffix):
        return text[: -len(suffix)], True
    return text, False


# The completion menu draws each row with one leading pad space, and anchors
# at the position the CURSOR had when completion started — together putting
# the menu text two columns right of the typed `/`. The anchor is fixed via
# the public `menu_position` hook (see `_build_prompt`); the pad has no
# knob, so the row builder is wrapped to render flush-left. If a
# prompt_toolkit upgrade changes the internals, the wrapper degrades to a
# one-column offset — never an error.
_ptk_menu_item_fragments = _ptk_menus._get_menu_item_fragments


def _menu_items_flush_left(
    completion: Completion, is_current_completion: bool, width: int, space_after: bool = False
) -> StyleAndTextTuples:
    fragments = _ptk_menu_item_fragments(completion, is_current_completion, width, space_after)
    if fragments and fragments[0][1] == " ":
        return fragments[1:]
    return fragments


_ptk_menus._get_menu_item_fragments = _menu_items_flush_left
