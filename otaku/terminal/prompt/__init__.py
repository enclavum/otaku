"""One submission in: everything between the keyboard and one submitted
line — the prompt_toolkit session (store-backed history via the backend,
the slash menu from `completion`, the shortcut keybindings), `Carry`
(what a shortcut hands across the prompt's exit), and `LineAssembler`
(the `\"\"\"` multiline convention). The loop only prompts, feeds, and
submits; and nothing here needs `Chat` — the prompt sits below the chat
surface.

A shortcut key exits the prompt with its command as the result, stashing
the in-progress text in `Carry` so the next prompt restores it; the keys
arrive as DATA (`build_prompt`'s `shortcuts` — `chat.bindings.SHORTCUTS`
passed as the mapping it already is, both spellings riding each entry,
so no seam translates by hand).

Multiline input follows the `\"\"\"` convention: a line starting with `\"\"\"`
opens a block that spans lines until a closing `\"\"\"` ends one; the text
between the delimiters (newlines preserved) is sent as a single message.
`LineAssembler` implements the state machine.
"""

from collections.abc import Callable, Mapping
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completion
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, completion_is_selected, has_completions
from prompt_toolkit.formatted_text import (
    ANSI,
    FormattedText,
    StyleAndTextTuples,
    to_formatted_text,
)
from prompt_toolkit.history import History
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import menus as _ptk_menus
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style

from otaku.backend.api import lore
from otaku.backend.api import settings as api_settings
from otaku.backend.session import Session
from otaku.formatting import flatten, truncate
from otaku.terminal.prompt.completion import SlashCompleter
from otaku.terminal.tty import statusline
from otaku.terminal.tty.render import command_tokens, message
from otaku.terminal.tty.theme import theme

PLACEHOLDER = FormattedText([("class:placeholder", "Send a message")])

# Ctrl+D submits /bye only on an empty line — the terminal convention.
# With a draft on the line it falls through to delete-forward, so readline
# muscle memory can never quit the session over a draft.
_EMPTY_LINE = Condition(lambda: not get_app().current_buffer.text)

_TRIPLE = '"""'


class Carry:
    """What a shortcut keybinding hands the run loop across the prompt's
    exit: the fact that a shortcut submitted the line, and the in-progress
    text to restore at the next prompt (still readable after `take_text`
    consumed it as the default — the shortcut branch erases exactly what
    was shown)."""

    def __init__(self) -> None:
        self.text = ""
        self.shortcut = False

    def take_text(self) -> str:
        text, self.text = self.text, ""
        return text

    def take_shortcut(self) -> bool:
        taken, self.shortcut = self.shortcut, False
        return taken


class LineAssembler:
    """Assembles triple-quoted multiline input, one line at a time.

    A block is a way to press Enter without submitting, and nothing more:
    what it collects is an ordinary prompt, read for its framing syntax
    like any other and told apart from a typed line by nothing downstream.

    Feed each input line via `feed()`. It returns None while a `\"\"\"` block
    is still open (the caller keeps prompting with the continuation
    prompt), otherwise the message. The one difference between the two
    cases is settled HERE, where it is known: a typed line is stripped, and
    a block keeps the whitespace at its edges — someone who opened a block
    to lay text out meant the layout."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self.in_block = False

    def feed(self, line: str) -> str | None:
        if self.in_block:
            before, closed = _cut_suffix(line, _TRIPLE)
            self._lines.append(before)
            if not closed:
                return None  # closing delimiter not seen yet — keep collecting
            text = "\n".join(self._lines)
            self.reset()
            return text
        head, opened = _cut_opener(line)
        if opened:
            rest, closed = _cut_suffix(line[len(head) + len(_TRIPLE) :], _TRIPLE)
            if closed:
                return head + rest  # a whole block on one line
            self._lines = [head + rest]
            self.in_block = True
            return None
        return line.strip()

    @property
    def prefix(self) -> str:
        """What an open block has collected, with the newline the next line
        will follow — so the completer can read the line being typed in the
        context of the message it belongs to. Empty when no block is open.

        Without it every continuation line looks like the start of a
        submission, and a `/` typed on one would open the command menu
        where only an inliner can go."""
        if not self.in_block:
            return ""
        return "\n".join(self._lines) + "\n"

    def reset(self) -> None:
        """Drop any partial block (used on Ctrl+C)."""
        self._lines = []
        self.in_block = False


class _SessionHistory(History):
    """prompt_toolkit input history backed by the session's store-backed
    channel: the last lines you submitted at the prompt, browsable with
    Up/Down across sessions. Best-effort on both sides — the session
    already never raises."""

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session

    def load_history_strings(self) -> list[str]:
        return self._session.history()  # already most-recent-first

    def store_string(self, string: str) -> None:
        self._session.record_history(string)


class _CommandLexer(Lexer):
    """Colors the commands in the line being typed — and leaves them
    colored once it is submitted, which is the only way a command that is
    not played ever shows styled: its typed line stays on screen with the
    output under it, never replaced by the grey block.

    The same highlighter the echo and the story browser use, so one rule
    decides what a command is and the line does not change appearance
    between being typed and being answered."""

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        def line(number: int) -> StyleAndTextTuples:
            return to_formatted_text(ANSI(message(document.lines[number], "user")))

        return line


def build_prompt(
    session: Session,
    carry: Carry,
    assembler: LineAssembler,
    *,
    shortcuts: Mapping[str, tuple[str, str]],
) -> PromptSession[str]:
    """The assembled prompt session: history, menu, style, the activity
    toolbar reading `session.status()`, and the shortcut keybindings —
    `shortcuts` maps a command token to its key in both spellings, the
    binding form and the menu caption (`chat.bindings.SHORTCUTS` passed
    as data, not an import: the prompt sits below the chat surface).

    Typing `/` opens the menu with every command (+ its help line as the
    meta column) and each keystroke filters it. prompt_toolkit gates the
    menu as `complete_while_typing AND NOT enable_history_search`, so with
    history search OFF the menu still shows only on slash lines, and the
    completer also returns nothing on prose as a second guard.

    History search stays OFF on purpose: with it on, Up on a line you have
    started typing searches for entries with THAT prefix and finds none,
    freezing on your draft. Off, Up/Down are plain previous/next entry."""
    # One test for "can a menu be open here?", shared by the
    # complete-while-typing filter and the menu bindings. It has to be built
    # here, not at import: the answer depends on whether a block is open.
    completer = SlashCompleter.build(
        lambda: assembler.prefix,
        cast=lambda: _cast(session),
        shortcuts={token: caption for token, (_key, caption) in shortcuts.items()},
        levels=lambda: api_settings.think_choices(session).levels,
        parameters=lambda: api_settings.parameter_names(session),
    )
    menu_line = Condition(
        lambda: completer.partial(get_app().current_buffer.document.text_before_cursor) is not None
    )
    prompt_session: PromptSession[str] = PromptSession(
        history=_SessionHistory(session),
        completer=completer,
        lexer=_CommandLexer(),
        key_bindings=_make_bindings(carry, menu_line, shortcuts),
        complete_while_typing=menu_line,
        enable_history_search=False,
        style=_prompt_style(),
        cursor=CursorShape.BLINKING_BEAM,
        bottom_toolbar=_activity_toolbar(session),
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
            completer.partial(prompt_session.default_buffer.document.text_before_cursor),
            prompt_session.default_buffer.cursor_position,
        )
    # Pre-select the first row whenever the menu (re)populates.
    prompt_session.default_buffer.on_completions_changed += lambda buf: _preselect_first(buf)
    return prompt_session


def _cast(session: Session) -> list[tuple[str, str]]:
    """The story's characters for the menu: (name, one-line description)
    rows, looked up live (`api.lore.cast` — cheap and empty-safe) so
    extraction and merges show the moment they land."""
    return [(c.name, truncate(flatten(c.description or ""), 48)) for c in lore.cast(session)]


def _prompt_style() -> Style:
    """The prompt's style sheet. Built when the prompt is, not at import:
    `class:command` names the shade the background decided, and the
    terminal is only asked once the session has started (see repl.run)."""
    # The accent the selected row is picked out by: the color a command
    # shows in once it is typed, so the row you are about to insert already
    # reads as what it will become. The same theme slot the highlighter
    # paints with, in the form prompt_toolkit wants.
    colors = theme()
    accent = f"fg:{colors.command.style}"
    return Style.from_dict(
        {
            "placeholder": f"fg:{colors.placeholder.style}",
            # The command menu: no colored panel — plain text on the terminal's
            # own background, dim descriptions, and the selected ROW (command and
            # description in ONE color) picked out by the accent instead of a
            # block. ANSI palette colors, so both dark and light themes work.
            "completion-menu": "bg:default",
            "completion-menu.completion": "bg:default fg:default",
            # `noreverse` matters: the default sheet marks the current row
            # `reverse`, and overriding only the colors leaves the flag on —
            # rendering as a colored BLOCK with swapped fg/bg.
            "completion-menu.completion.current": f"bg:default {accent} noreverse",
            "completion-menu.meta.completion": "bg:default fg:ansibrightblack",
            "completion-menu.meta.completion.current": f"bg:default {accent} noreverse",
            "scrollbar.background": "bg:default",
            "scrollbar.button": "bg:ansibrightblack",
            # prompt_toolkit styles the bottom toolbar `reverse` by default — a
            # full-width grey bar; noreverse leaves it plain, dim like the
            # pinned row it hands off to.
            "bottom-toolbar": "noreverse",
            "bottom-toolbar.text": "noreverse fg:ansibrightblack",
        }
    )


def _activity_toolbar(session: Session) -> Callable[[], FormattedText]:
    """The prompt's activity line: what the background worker is doing, or
    blank when idle. Always present — the row is reserved for the whole
    prompt, so it never reflows the screen. `statusline.render`, not a
    local format string: the pinned row draws the same line on the same
    terminal row, and any difference shows as a twitch when a reply starts
    streaming."""

    def render() -> FormattedText:
        return FormattedText([("class:bottom-toolbar.text", statusline.render(session.status()))])

    return render


# ---------- keybindings ----------


def _make_bindings(
    carry: Carry, menu_line: Condition, shortcuts: Mapping[str, tuple[str, str]]
) -> KeyBindings:
    kb = KeyBindings()
    for command, (key, _caption) in shortcuts.items():
        # Ctrl+D quits only on an empty line — the terminal convention;
        # with a draft it falls through to delete-forward, so readline
        # muscle memory can never quit the session over a draft.
        kb.add(key, filter=_EMPTY_LINE if key == "c-d" else True)(_submit_shortcut(command, carry))

    # Tab always FILLS a pre-selected row. Enter fills it too when
    # anything can follow the row — a parameter, required or optional, or
    # a subcommand — and otherwise sends, because a command that takes
    # nothing is complete the moment it is chosen. The default bindings
    # can't do
    # either: they treat the highlight as already-inserted text, but
    # _preselect_first only sets the index, so the buffer still holds
    # exactly what was typed.
    @kb.add("enter", filter=completion_is_selected)
    def _accept_on_enter(event: Any) -> None:
        if not _accept_selection(event.current_buffer):
            event.current_buffer.validate_and_handle()

    @kb.add("tab", filter=completion_is_selected)
    def _fill_on_tab(event: Any) -> None:
        _accept_selection(event.current_buffer)

    # Ctrl+/ — the terminal sends 0x1f for the physical slash key in ANY
    # layout, so this is the slash for a keyboard where `/` itself is a
    # shifted reach (ЙЦУКЕН puts `.` there). It just types one: the menus
    # open and filter exactly as if it were typed.
    @kb.add("c-_")
    def _type_slash(event: Any) -> None:
        event.current_buffer.insert_text("/")

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
    @kb.add("backspace", filter=menu_line)
    def _bs_refilter(event: Any) -> None:
        event.current_buffer.delete_before_cursor(count=event.arg)
        if menu_line():
            event.current_buffer.start_completion(select_first=False)
        else:
            event.current_buffer.cancel_completion()

    return kb


def _submit_shortcut(command: str, carry: Carry) -> Callable[[Any], None]:
    """Exit the prompt directly with `command` as the result — without
    rendering it into the buffer (no visible flash) or running it through
    `validate_and_handle` (no history append)."""

    def handler(event: Any) -> None:
        carry.text = event.current_buffer.text  # restore the line afterwards
        carry.shortcut = True  # the run loop must not feed this into a """ block
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
    so a stray state can never raise.

    True when the row can take something after it: it took a space with
    it, so the rest can be typed straight on and a subcommand's own menu
    opens. Optional parameters count — the row was chosen over the bare
    line, and Enter on the line as it stands still sends. False only when
    nothing can follow, and then Enter may send at once."""
    state = buff.complete_state
    if state is None or state.complete_index is None or not state.completions:
        return False
    completion = state.completions[state.complete_index]
    buff.apply_completion(completion)
    if not getattr(completion, "takes_argument", False):
        return False
    buff.insert_text(" ")
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


def _menu_anchor_index(partial: str | None, cursor: int) -> int | None:
    """Document index the completion menu anchors at: the start of the token
    being completed, so the menu opens right under the `/`. An empty partial
    is a menu about to be filtered by a token not yet typed, and anchors at
    the cursor. None = default anchor, for a line neither menu opens on."""
    if partial is None:
        return None
    return cursor - len(partial)


def _cut_opener(line: str) -> tuple[str, bool]:
    """The text a `\"\"\"` opener follows, and whether the line opens a block
    at all. Usually nothing precedes it — but a COMMAND may (`/system
    \"\"\"`), because a multiline argument is the obvious reason to want a
    block, and the delimiters would otherwise land in that argument as
    text. The command is kept and the quotes dropped, so the block reads
    as the one long line the writer meant to type.

    Only a real command opens one, and only with the quotes right after
    it: `/ooc the docstring is \"\"\"x\"\"\"` is prose about quotes, and its
    line has to survive as typed."""
    if line.startswith(_TRIPLE):
        return "", True
    head, quotes, _ = line.partition(_TRIPLE)
    if quotes and head.rstrip(" ") in command_tokens():
        return head, True
    return line, False


def _cut_suffix(text: str, suffix: str) -> tuple[str, bool]:
    if text.endswith(suffix):
        return text[: -len(suffix)], True
    return text, False


# The completion menu draws each row with one leading pad space, and anchors
# at the position the CURSOR had when completion started — together putting
# the menu text two columns right of the typed `/`. The anchor is fixed via
# the public `menu_position` hook (see `build_prompt`); the pad has no
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
