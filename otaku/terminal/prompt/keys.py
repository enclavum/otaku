"""The prompt's keys: the shortcut keys that submit a command, the
canvas's paragraph and history walk, the slash menu's Enter, Tab, Esc
and Backspace, ↑/↓ through the menu or the history, and the answers'
keys while their rows are shown (`make_bindings`)."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, completion_is_selected, has_completions
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from otaku.terminal.prompt.answers import AnswerMenu
from otaku.terminal.prompt.lines import Carry

# PROTOTYPE: Ctrl+Enter told from Enter where the terminal reports it —
# xterm's modifyOtherKeys form and the CSI u form, which prompt_toolkit
# reads as Enter — spelled as Windows' console input spells it (Esc, then
# Ctrl+J), so one binding opens a paragraph on every platform
# (`make_bindings`)
_CONTROL_ENTER = ("\x1b[27;5;13~", "\x1b[13;5u")
for _sequence in _CONTROL_ENTER:
    ANSI_SEQUENCES[_sequence] = (Keys.Escape, Keys.ControlJ)

# Ctrl+D submits /quit only on an empty line — the terminal convention.
# With a draft on the line it falls through to delete-forward, so readline
# muscle memory can never quit the app over a draft.
_EMPTY_LINE = Condition(lambda: not get_app().current_buffer.text)


def make_bindings(
    carry: Carry,
    menu_line: Condition,
    shortcuts: Mapping[str, tuple[str, str]],
    answers: AnswerMenu,
    history: Callable[[], Sequence[str]],
) -> KeyBindings:
    kb = KeyBindings()
    # PROTOTYPE: in the canvas Enter sends, as everywhere, and Ctrl+Enter
    # opens a paragraph — the blank line the story stores — where the
    # terminal tells it from Enter (Windows always; elsewhere where it
    # honours modifyOtherKeys, `_CONTROL_ENTER`), as does Ctrl+J on every
    # terminal, which some send for Ctrl+Enter. A line of a command or a
    # direction is one line: the paragraph key sends it.
    canvas_keys = Condition(lambda: carry.canvas_keys)

    @kb.add("escape", "c-j", filter=canvas_keys)
    @kb.add("c-j", filter=canvas_keys)
    def _paragraph(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.text.lstrip().startswith("/") and "\n" not in buffer.text:
            buffer.validate_and_handle()
        else:
            buffer.insert_text("\n\n")

    # The paragraph Ctrl+Enter opened is one keystroke, so one Backspace
    # takes it back: right after a blank line, both its newlines go.
    @kb.add(
        "backspace",
        filter=canvas_keys
        & Condition(lambda: get_app().current_buffer.document.text_before_cursor.endswith("\n\n")),
    )
    def _join_paragraph(event: Any) -> None:
        event.current_buffer.delete_before_cursor(count=2)

    def _empty_paragraph() -> bool:
        # the caret at the start of an empty paragraph: nothing typed yet,
        # or a blank line just opened at the end of what was
        document = get_app().current_buffer.document
        before = document.text_before_cursor
        if document.text_after_cursor.strip():
            return False
        return not before.strip() or before.endswith("\n\n")

    @kb.add(
        "/",
        filter=Condition(lambda: carry.canvas_keys and not carry.command_paragraph)
        & Condition(_empty_paragraph),
    )
    def _to_command(event: Any) -> None:
        buffer = event.current_buffer
        carry.shown = buffer.text
        carry.draft = buffer.text.rstrip()
        carry.to_command = True
        if not carry.in_text:
            # outside the text there is nothing to stand in: the prompt's
            # own row goes, and the command line takes its place
            event.app.erase_when_done = True
        event.app.exit(result="")

    command_paragraph = Condition(lambda: carry.command_paragraph)
    bare_slash = Condition(lambda: get_app().current_buffer.text == "/")

    @kb.add("escape", filter=command_paragraph & ~has_completions)
    @kb.add("backspace", filter=command_paragraph & bare_slash)
    def _from_command(event: Any) -> None:
        carry.shown = event.current_buffer.text
        carry.from_command = True
        # everything it drew goes — the line, and a menu open under it — the
        # caret back where it began, so nothing is left to count
        event.app.erase_when_done = True
        event.app.exit(result="")

    for command, (key, _caption) in shortcuts.items():
        # Ctrl+D quits only on an empty line — the terminal convention;
        # with a draft it falls through to delete-forward, so readline
        # muscle memory can never quit the app over a draft.
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
        carry.recalled = carry.recalled or not event.current_buffer.complete_state
        _menu_or_history_up(event.current_buffer, event.arg)

    @kb.add("down")
    def _down(event: Any) -> None:
        carry.recalled = carry.recalled or not event.current_buffer.complete_state
        _menu_or_history_down(event.current_buffer, event.arg)

    # PROTOTYPE: in the canvas Up/Down walk the history across both places
    # (`Carry.walk`) — the command menu still theirs while it is open
    def _walk(event: Any, step: int) -> None:
        buffer = event.current_buffer
        if buffer.complete_state:
            if step > 0:
                buffer.complete_previous(count=event.arg)
            else:
                buffer.complete_next(count=event.arg)
            return
        entries = history()  # most recent first
        at = carry.walk + step
        if not -1 <= at < len(entries):
            return
        if carry.walk == -1:  # leaving the reader's own draft: keep it
            carry.walk_draft = buffer.text
            carry.walk_draft_command = carry.command_paragraph
        carry.walk = at
        if at == -1:
            text, command = carry.walk_draft, carry.walk_draft_command
        else:
            text = entries[at]
            command = text.startswith("/")
        if command == carry.command_paragraph:
            carry.recalled = at != -1
            buffer.document = Document(text, len(text))
            return
        # the other place: the prompt hands over, as `/` and Esc do, and
        # the next prompt opens with the entry
        carry.recall = text
        carry.shown = buffer.text
        if command:
            # the story's draft waits for the command's end
            carry.draft = "" if carry.walk_draft_command else carry.walk_draft.rstrip()
            carry.to_command = True
            if not carry.in_text:
                event.app.erase_when_done = True
        else:
            carry.from_command = True
            event.app.erase_when_done = True
        event.app.exit(result="")

    walking = Condition(lambda: carry.canvas_keys)

    @kb.add("up", filter=walking)
    def _walk_up(event: Any) -> None:
        _walk(event, 1)

    @kb.add("down", filter=walking)
    def _walk_down(event: Any) -> None:
        _walk(event, -1)

    # The command menu closes on Esc (the default Escape is a meta prefix
    # that does nothing visible here)…
    @kb.add("escape", filter=has_completions, eager=True)
    def _close_menu(event: Any) -> None:
        event.current_buffer.cancel_completion()

    # …and does NOT close on backspace. prompt_toolkit restarts the menu
    # only on text INSERTS, so a plain backspace while filtering would
    # dismiss it; this deletes and re-opens while the line is a command.
    @kb.add("backspace", filter=menu_line & ~(command_paragraph & bare_slash))
    def _bs_refilter(event: Any) -> None:
        event.current_buffer.delete_before_cursor(count=event.arg)
        if menu_line():
            event.current_buffer.start_completion(select_first=False)
        else:
            event.current_buffer.cancel_completion()

    # The answers above the line, while they are shown: ↑/↓ walk them
    # (added after the history keys, so they win while the rows are up),
    # Enter sends the one under the cursor, → takes it into the line,
    # Esc dismisses. → because it is the one key every terminal reports
    # that means nothing on an empty line: Shift+Enter and Ctrl+Enter
    # are plain Enter wherever the terminal encodes no modifiers.
    rows_up = Condition(answers.shown)

    @kb.add("up", filter=rows_up)
    def _answer_up(event: Any) -> None:
        answers.move(-1)

    @kb.add("down", filter=rows_up)
    def _answer_down(event: Any) -> None:
        answers.move(1)

    @kb.add("enter", filter=rows_up)
    def _answer_send(event: Any) -> None:
        event.current_buffer.insert_text(answers.chosen())
        event.current_buffer.validate_and_handle()

    @kb.add("right", filter=rows_up)
    def _answer_take(event: Any) -> None:
        event.current_buffer.insert_text(answers.chosen())

    @kb.add("escape", filter=rows_up, eager=True)
    def _answer_dismiss(event: Any) -> None:
        answers.dismissed = True

    # ^C while the story stands on a question — rows opened for this
    # prompt, shown or not — erases the prompt, rows and all, instead of
    # leaving the aborted line where the next prompt draws the rows again
    # under it. The flag is prompt_toolkit's own, read at its final
    # redraw; the run loop resets it (`terminal.run`).
    @kb.add("c-c", filter=Condition(lambda: bool(answers.rows)))
    def _answer_abort(event: Any) -> None:
        event.app.erase_when_done = True
        event.app.exit(exception=KeyboardInterrupt(), style="class:aborting")

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
