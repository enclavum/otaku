"""The prompt session: everything between the keyboard and one submitted
line, assembled (`build_prompt`) — the store-backed history, the slash
menu from `completion`, the lexer that colors a command as it is typed,
the style, the activity toolbar, the keys (`keys`) and the caret
(`caret`) — and the completion menu drawn the way the terminal draws it:
its first row pre-selected, anchored at the token, flush left."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import (
    ANSI,
    FormattedText,
    StyleAndTextTuples,
    split_lines,
    to_formatted_text,
)
from prompt_toolkit.history import History
from prompt_toolkit.layout import menus as _ptk_menus
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.renderer import Renderer
from prompt_toolkit.styles import Style

from otaku.formatting import flatten, truncate
from otaku.terminal.api import Api
from otaku.terminal.api.models import ModelSettings
from otaku.terminal.prompt.answers import AnswerMenu
from otaku.terminal.prompt.caret import Caret, caret_painter
from otaku.terminal.prompt.completion import SlashCompleter
from otaku.terminal.prompt.keys import make_bindings
from otaku.terminal.prompt.lines import Carry, LineAssembler
from otaku.terminal.tty import HIDE_CURSOR, statusline
from otaku.terminal.tty.render import message
from otaku.terminal.tty.theme import theme

# The empty line's hint in chat mode; story mode shows its caret instead
# (`caret.Caret`).
CHAT_PLACEHOLDER = FormattedText([("class:placeholder", "Send a message")])


class _AppHistory(History):
    """prompt_toolkit input history backed by the app's store-backed
    channel: the last lines you submitted at the prompt, browsable with
    Up/Down across sessions. Best-effort on both sides — the app
    already never raises."""

    def __init__(self, api: Api) -> None:
        super().__init__()
        self._api = api

    def load_history_strings(self) -> list[str]:
        return self._api.history.list()  # already most-recent-first

    def store_string(self, string: str) -> None:
        self._api.history.create(text=string)


class _CommandLexer(Lexer):
    """Colors the commands in the line being typed — and leaves them
    colored once it is submitted, which is the only way a command that is
    not played ever shows styled: its typed line stays on screen with the
    output under it, never replaced by the grey block.

    The same highlighter the echo and the story browser use, so one rule
    decides what a command is and the line does not change appearance
    between being typed and being answered."""

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        # coloured whole, then cut into lines: a word with the model runs
        # on to the end of what is typed, across its line breaks
        lines = list(split_lines(to_formatted_text(ANSI(message(document.text, "user")))))

        def line(number: int) -> StyleAndTextTuples:
            return lines[number] if number < len(lines) else []

        return line


def build_prompt(
    api: Api,
    carry: Carry,
    assembler: LineAssembler,
    *,
    story: Callable[[], int | None],
    can_attach: Callable[[], bool],
    shortcuts: Mapping[str, tuple[str, str]],
    aside: Callable[[], str],
    note: Callable[[], str] = lambda: "",
) -> tuple[PromptSession[str], AnswerMenu]:
    """The assembled prompt session and the answer menu it draws above
    its line: history, menu, style, the activity toolbar reading `note()`
    and the worker's line with `aside()` against its right edge, and the
    shortcut keybindings — the story played read off `story()`, and
    whether a picture may ride the line off `can_attach()`, both the
    caller's to know;
    `shortcuts` maps a command token to its key in both spellings, the
    binding form and the menu caption (`commands.SHORTCUTS` passed
    as data, not an import: the prompt sits below the commands).

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
        cast=lambda: _cast(api, story()),
        pictures_accepted=can_attach,
        shortcuts={token: caption for token, (_key, caption) in shortcuts.items()},
        levels=lambda: _model_settings(api).choices.levels,
        parameters=lambda: [p.name for p in _model_settings(api).parameters if p.supported],
    )
    menu_line = Condition(
        lambda: completer.partial(get_app().current_buffer.document.text_before_cursor) is not None
    )
    answers = AnswerMenu(lambda: _answers(api, story()))
    prompt_session: PromptSession[str] = PromptSession(
        history=_AppHistory(api),
        completer=completer,
        lexer=_CommandLexer(),
        key_bindings=make_bindings(carry, menu_line, shortcuts, answers, api.history.list),
        complete_while_typing=menu_line,
        enable_history_search=False,
        style=_prompt_style(),
        cursor=Caret(carry),
        bottom_toolbar=_activity_toolbar(api, aside, note),
    )
    # A prompt standing in the story's text (`Carry.in_text`) ends leaving
    # what it drew, the caret back where its drawing began: the ledger
    # writes the text's last line over itself and erases what follows in
    # one write (`ledger.resume_tail`), where the renderer's own erase,
    # written apart, would show the text gone a moment. Any other prompt
    # that erases when done erases as ever.
    renderer = prompt_session.app.renderer
    erase = renderer.erase

    def _erase(leave_alternate_screen: bool = True) -> None:
        if carry.in_text:
            _back_to_start(renderer)
        else:
            erase(leave_alternate_screen)

    renderer.erase = _erase  # type: ignore[method-assign]
    prompt_session.app.after_render += caret_painter(carry)
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
    return prompt_session, answers


def _back_to_start(renderer: Renderer) -> None:
    """`Renderer.erase` without the erase: the caret back where the
    drawing began, the drawing left standing. The cursor's place in it is
    the renderer's own record (`_cursor_pos`). Hidden on the way and until
    the ledger shows it at the text's end (`ledger.resume_tail`) — written
    past the renderer's record of it, which goes on believing it shown, so
    its next frame hides it first as ever."""
    output = renderer.output
    output.write_raw(HIDE_CURSOR)
    output.cursor_backward(renderer._cursor_pos.x)
    output.cursor_up(renderer._cursor_pos.y)
    output.reset_attributes()
    output.enable_autowrap()
    renderer.reset()
    output.flush()


def _answers(api: Api, story_id: int | None) -> Sequence[str]:
    """The answers to pick from, while the story stands on a question
    with a fixed set (`Story.question`) — read as each prompt opens: the
    line played next answers the question, and the rows go."""
    if story_id is None:
        return ()
    question = api.stories.get(story_id).question
    return question.options if question is not None else ()


def _cast(api: Api, story_id: int | None) -> list[tuple[str, str]]:
    """The story's characters for the menu: (name, one-line description)
    rows, looked up live (`api.stories.characters` — empty-safe) so
    extraction and merges show the moment they land."""
    if story_id is None:
        return []
    cast = api.stories.characters.list(story_id)
    return [(c.name, truncate(flatten(c.description or ""), 48)) for c in cast]


def _model_settings(api: Api) -> ModelSettings:
    """The model in use's own settings, for the menus that offer them."""
    model = api.app.get().model
    settings = api.providers.models.settings
    if model is None:
        return settings.get("", "")
    return settings.get(model.provider, model.name)


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
            # The answer rows above the prompt: the one under the cursor in
            # the accent, as the command menu picks its row out.
            "answer": "fg:default",
            "answer.current": accent,
        }
    )


def _activity_toolbar(
    api: Api, aside: Callable[[], str], note: Callable[[], str]
) -> Callable[[], FormattedText]:
    """The prompt's activity line: `note()` — the last reply's stats in
    story mode — and what the background worker is doing, either blank,
    and `aside()` against the right edge. Always present —
    the row is reserved for the whole prompt, so it never reflows the
    screen. `statusline.render`, not a local format string: the pinned row
    draws the same line on the same terminal row, and any difference shows
    as a twitch when a reply starts streaming."""

    def render() -> FormattedText:
        width = get_app().output.get_size().columns
        line = statusline.render(api.app.status.get(), aside(), width, note())
        return FormattedText([("class:bottom-toolbar.text", line)])

    return render


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


def _menu_anchor_index(partial: str | None, cursor: int) -> int | None:
    """Document index the completion menu anchors at: the start of the token
    being completed, so the menu opens right under the `/`. An empty partial
    is a menu about to be filtered by a token not yet typed, and anchors at
    the cursor. None = default anchor, for a line neither menu opens on."""
    if partial is None:
        return None
    return cursor - len(partial)


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
