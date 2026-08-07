"""The skeleton every full-screen picker shares, and its widgets.

`ListScreen` owns what all three pickers repeat: the `/`-filter state
machine, the clamped cursor, the standard keys (navigation, Enter, Escape,
Backspace, typing) funneled into overridable hooks, the pane-width
arithmetic for a list-plus-preview split, the left-pane chrome, the
preview panel with its edit-buffer swap, and the Application finishing
touches. A subclass fills the text of each pane and what Enter, Escape,
and the letter keys mean; everything visual stays the subclass's own.

The widgets below the class are the pieces it is assembled from — the
shared style sheet, `bordered_box` for dialogs and preview panes,
`text_line` for the one-line chrome rows — plus the terminal-size helpers
and the blank-line-preserving `wrap_text` the previews use.
"""

import textwrap
from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, FilterOrBool
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyBindingsBase
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    AnyContainer,
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl, UIControl
from prompt_toolkit.layout.dimension import AnyDimension, D
from prompt_toolkit.styles import Style

_PREVIEW_OUTER_GAP = 3  # cols between items pane and preview border
_PREVIEW_INNER_PAD = 2  # cols inside the border, both sides


class ListScreen:
    """Base of the pickers: filter and cursor mechanics plus the shared
    assembly. The standard keys call the `_on_*` hooks; the pane renderers
    are the subclass's."""

    def __init__(self) -> None:
        self.in_filter: bool = False
        self.query: str = ""
        self.notice: str = ""
        # The pane width the rows on screen were built from — compared
        # after each frame, see `_resync_rows`.
        self._row_width_used: int | None = None

    # ---------- what a subclass fills ----------

    def _header_text(self) -> StyleAndTextTuples:
        raise NotImplementedError

    def _items_text(self) -> StyleAndTextTuples:
        raise NotImplementedError

    def _help_text(self) -> StyleAndTextTuples:
        raise NotImplementedError

    def _panel_header_text(self) -> StyleAndTextTuples:
        raise NotImplementedError  # only screens using `_preview_panel`

    def _preview_text(self) -> StyleAndTextTuples:
        raise NotImplementedError  # only screens using `_preview_panel`

    def _rows_count(self) -> int:
        raise NotImplementedError

    def _cursor(self) -> int:
        raise NotImplementedError

    def _set_cursor(self, value: int) -> None:
        raise NotImplementedError

    def _refilter(self) -> None:
        raise NotImplementedError

    def _on_enter(self) -> None:
        raise NotImplementedError

    def _on_escape(self) -> None:
        raise NotImplementedError

    def _on_key(self, data: str) -> None:
        """A printable key outside the filter — the subclass's letter
        commands. Nothing by default."""

    def _split(self) -> tuple[int, int]:
        """(list weight, preview weight) — overridden where the split
        changes per view."""
        return (1, 1)

    # ---------- filter + cursor mechanics ----------

    def _filter_text(self) -> StyleAndTextTuples:
        if not self.in_filter:
            return [("", "")]
        return [
            ("class:filter", "  filter: "),
            ("class:filter.query", self.query),
        ]

    def _move_cursor(self, delta: int) -> None:
        self._set_cursor(max(0, min(self._rows_count() - 1, self._cursor() + delta)))

    def _open_filter(self) -> None:
        self.in_filter = True
        self.query = ""
        self._refilter()

    def _clear_filter(self) -> bool:
        """Close an active filter (the first thing Escape does everywhere).
        Returns whether there was one to close."""
        if not self.in_filter:
            return False
        self.in_filter = False
        self.query = ""
        self._refilter()
        return True

    def _backspace_filter(self) -> None:
        """Backspace while filtering: shorten the query, or close an
        emptied filter. A no-op outside the filter."""
        if not self.in_filter:
            return
        if not self.query:
            self.in_filter = False
        else:
            self.query = self.query[:-1]
        self._refilter()

    def _type(self, data: str) -> None:
        """A printable key: filter input while filtering, `/` opens the
        filter, anything else is a letter command."""
        if self.in_filter:
            self.query += data
            self._refilter()
        elif data == "/":
            self._open_filter()
        else:
            self._on_key(data)

    def _emit_row(
        self, out: StyleAndTextTuples, selected: bool, row: str, *, dim: bool = False
    ) -> None:
        """One list row, padded to the full items pane: every row ends in
        the same column, so the selection band is a rectangle and the gap
        before the preview's border is `_preview_gap` on every line — not
        only on the rows long enough to fill their width."""
        prefix = "  > " if selected else "    "
        base = "row.dim" if dim else "row"
        style = f"class:{base}.selected" if selected else f"class:{base}"
        out.append((style, (prefix + row).ljust(self._max_row_content_width()) + "\n"))

    # ---------- width arithmetic ----------

    def _pane_cols(self) -> tuple[int, int]:
        """(list pane cols, preview inner cols) for the current split — kept
        in step with the VSplit weights so rows and the preview wrap to the
        widths they actually get."""
        avail = max(24, term_cols() - _PREVIEW_OUTER_GAP)
        lw, pw = self._split()
        left = max(12, avail * lw // (lw + pw))
        preview_inner = max(10, (avail - left) - 2 - _PREVIEW_INNER_PAD * 2)
        return left, preview_inner

    def _max_row_content_width(self) -> int:
        """What a list row may occupy: the WHOLE items pane, measured off
        the rendered window. Rows fill it exactly, so the only space left
        between them and the preview's border is `_preview_gap` — the
        same three columns in every picker, at every terminal size. Off
        the computed width instead, prompt_toolkit's weight rounding
        would move that gap by a column as the terminal resizes."""
        self._row_width_used = self._measured("_items_window", self._pane_cols()[0])
        return self._row_width_used

    def _resync_rows(self) -> None:
        """After a frame: the measurement is only current once the window
        has been drawn, so the rows in a frame that RESIZED the pane were
        laid out at the previous width — the first frame of all (nothing
        measured yet), and the frame a view flips the split on (the
        message list is 2:1 where the story list is 1:1). Ask for one
        more frame when the two disagree; the second one agrees, so it
        settles there instead of redrawing forever."""
        if self._row_width_used is None:
            return
        window: Window | None = getattr(self, "_items_window", None)
        if window is None:
            return
        info = window.render_info
        if info is not None and 0 < info.window_width != self._row_width_used:
            get_app().invalidate()

    def _preview_inner_width(self) -> int:
        """The preview's content width — measured the same way, because a
        line padded to a computed width one column too wide wraps its
        last character."""
        return self._measured("_preview_window", self._pane_cols()[1])

    def _measured(self, attr: str, fallback: int) -> int:
        """A named window's rendered width, `fallback` (the split
        arithmetic) until the first frame has drawn it."""
        window: Window | None = getattr(self, attr, None)
        if window is not None:
            info = window.render_info
            if info is not None and info.window_width > 0:
                return int(info.window_width)
        return fallback

    # ---------- assembly ----------

    def _standard_keys(self, kb: KeyBindings, *, when: FilterOrBool = True) -> None:
        """The keys every picker answers, wired to the hooks; `when` guards
        them all (e.g. "no dialog is up")."""

        @kb.add("up", filter=when)
        def _up(event: Any) -> None:
            self._move_cursor(-1)

        @kb.add("down", filter=when)
        def _down(event: Any) -> None:
            self._move_cursor(1)

        @kb.add("pageup", filter=when)
        def _pgup(event: Any) -> None:
            self._move_cursor(-page_step())

        @kb.add("pagedown", filter=when)
        def _pgdn(event: Any) -> None:
            self._move_cursor(page_step())

        @kb.add("home", filter=when)
        def _home(event: Any) -> None:
            self._move_cursor(-self._rows_count())

        @kb.add("end", filter=when)
        def _end(event: Any) -> None:
            self._move_cursor(self._rows_count())

        @kb.add("enter", filter=when)
        def _enter(event: Any) -> None:
            self._on_enter()

        @kb.add("escape", eager=True, filter=when)
        def _esc(event: Any) -> None:
            self._on_escape()

        @kb.add("backspace", filter=when)
        def _bs(event: Any) -> None:
            self._backspace_filter()

        @kb.add(Keys.Any, filter=when)
        def _any(event: Any) -> None:
            data = event.data
            if data and len(data) == 1 and data.isprintable():
                self._type(data)

    def _make_items_control(
        self, *, cursor_line: Callable[[], int] | None = None, focusable: bool = False
    ) -> FormattedTextControl:
        """The list's control, scroll kept on the cursor's visual row.
        Focusable when focus must have somewhere to return to after an
        edit buffer gives it back."""
        return FormattedTextControl(
            text=self._items_text,
            get_cursor_position=lambda: Point(0, (cursor_line or self._cursor)()),
            show_cursor=False,
            focusable=focusable,
        )

    def _list_pane(
        self,
        items_window: Window,
        *,
        width: AnyDimension = None,
        notice: bool = False,
    ) -> AnyContainer:
        """The chrome around the list: header, blank, the filter line while
        filtering, the items, an optional notice line, blank, help."""
        # Kept so `_max_row_content_width` can measure what the rows
        # actually got; a picker assembling its own pane records it too.
        self._items_window = items_window
        rows: list[AnyContainer] = [
            text_line(self._header_text, style="class:header"),
            Window(height=1, char=" ", always_hide_cursor=True),
            text_line(self._filter_text, filter=Condition(lambda: self.in_filter)),
            items_window,
        ]
        if notice:
            rows.append(
                text_line(
                    lambda: [("class:notice", "  " + self.notice if self.notice else "")],
                    filter=Condition(lambda: bool(self.notice)),
                )
            )
        rows += [
            Window(height=1, char=" ", always_hide_cursor=True),
            text_line(self._help_text, style="class:help"),
        ]
        return HSplit(rows, width=width)

    def _preview_panel(
        self,
        *,
        header_filter: FilterOrBool,
        editing: Condition,
        edit_window: Window,
    ) -> AnyContainer:
        """ONE box for the preview: a fixed header above a body that is the
        preview text — or, while editing, the live buffer in the same spot.
        The header never moves, so editing starts exactly where the text
        already is."""
        panel_header = ConditionalContainer(
            Window(
                FormattedTextControl(text=self._panel_header_text, show_cursor=False),
                wrap_lines=True,
                dont_extend_height=True,
                always_hide_cursor=True,
                style="class:preview.body",
            ),
            filter=header_filter,
        )
        self._preview_window = Window(
            FormattedTextControl(text=self._preview_text, show_cursor=False),
            wrap_lines=True,
            always_hide_cursor=True,
            style="class:preview.body",
        )
        panel_body = HSplit(
            [
                panel_header,
                ConditionalContainer(self._preview_window, filter=~editing),
                ConditionalContainer(edit_window, filter=editing),
            ]
        )
        return bordered_box(
            panel_body,
            width=D(weight=1),
            style="class:preview.body",
            inner_pad=_PREVIEW_INNER_PAD,
        )

    def _preview_gap(self) -> Window:
        return Window(width=_PREVIEW_OUTER_GAP, char=" ", always_hide_cursor=True)

    def _finish_app(
        self,
        root: AnyContainer,
        bindings: KeyBindingsBase,
        style: Style,
        floats: list[AnyContainer],
    ) -> Application[None]:
        """The Application, floats mounted, with the snappy-Esc tuning every
        picker wants (the 1.0s/0.5s defaults feel laggy)."""
        content = FloatContainer(content=root, floats=[Float(content=f) for f in floats])
        app: Application[None] = Application(
            layout=Layout(content),
            key_bindings=bindings,
            style=style,
            mouse_support=False,
            full_screen=True,
            enable_page_navigation_bindings=False,
        )
        app.timeoutlen = 0.05
        app.ttimeoutlen = 0.05
        app.after_render += lambda _app: self._resync_rows()
        return app


# Style entries shared between the pickers. Each merges its own row /
# preview / dialog overrides on top of this dict.
BASE_STYLE: dict[str, str] = {
    "": "fg:#000000 bg:#ffffff",
    "header": "bold fg:#303030 bg:#ffffff",
    "muted": "fg:#767676 bg:#ffffff",
    "filter": "fg:#767676 bg:#ffffff",
    "filter.query": "bold fg:#303030 bg:#ffffff",
    "help": "fg:#767676 bg:#ffffff",
    "frame.border": "fg:#767676 bg:#ffffff",
    "dialog.border": "fg:#767676 bg:#ffffff",
    "dialog.title": "bold fg:#000000 bg:#ffffff",
    "dialog.body": "fg:#000000 bg:#ffffff",
    "dialog.muted": "fg:#767676 bg:#ffffff",
}


def term_cols() -> int:
    """Terminal width for layout math, with a narrow-terminal fallback."""
    try:
        return get_app().output.get_size().columns
    except Exception:
        return 120


def page_step() -> int:
    """Rows to move for PageUp/PageDown: a screenful minus the chrome rows."""
    return max(1, get_app().output.get_size().rows - 5)


def wrap_text(text: str, width: int) -> list[str]:
    """Wrap preview prose to `width`, preserving blank lines."""
    if width <= 0:
        return [text]
    out: list[str] = []
    for line in text.splitlines() or [""]:
        if not line:
            out.append("")
            continue
        wrapped = textwrap.wrap(line, width=width, break_long_words=True, break_on_hyphens=False)
        out.extend(wrapped or [""])
    return out


def bordered_box(
    control: UIControl | AnyContainer,
    *,
    width: AnyDimension = None,
    height: AnyDimension = None,
    style: str = "",
    border_style: str = "class:frame.border",
    inner_pad: int = 2,
    wrap: bool = True,
) -> AnyContainer:
    """Box with sharp single-line corners (┌─┐│└┘), `inner_pad`-col side
    padding, and one blank row top and bottom inside the border. A bare
    UIControl is wrapped in a Window; a prebuilt container is boxed as-is."""
    inner_content: AnyContainer = (
        Window(content=control, wrap_lines=wrap, always_hide_cursor=True, style=style)
        if isinstance(control, UIControl)
        else control
    )
    middle = VSplit(
        [
            Window(width=inner_pad, char=" ", style=style, always_hide_cursor=True),
            inner_content,
            Window(width=inner_pad, char=" ", style=style, always_hide_cursor=True),
        ]
    )
    inner = HSplit(
        [
            Window(height=1, char=" ", style=style, always_hide_cursor=True),
            middle,
            Window(height=1, char=" ", style=style, always_hide_cursor=True),
        ]
    )
    middle_row = VSplit(
        [
            Window(width=1, char="│", style=border_style, always_hide_cursor=True),
            inner,
            Window(width=1, char="│", style=border_style, always_hide_cursor=True),
        ]
    )
    return HSplit(
        [
            VSplit(
                [
                    Window(
                        width=1, height=1, char="┌", style=border_style, always_hide_cursor=True
                    ),
                    Window(char="─", height=1, style=border_style, always_hide_cursor=True),
                    Window(
                        width=1, height=1, char="┐", style=border_style, always_hide_cursor=True
                    ),
                ],
                height=1,
            ),
            middle_row,
            VSplit(
                [
                    Window(
                        width=1, height=1, char="└", style=border_style, always_hide_cursor=True
                    ),
                    Window(char="─", height=1, style=border_style, always_hide_cursor=True),
                    Window(
                        width=1, height=1, char="┘", style=border_style, always_hide_cursor=True
                    ),
                ],
                height=1,
            ),
        ],
        width=width,
        height=height,
    )


def text_line(
    get_text: Callable[[], StyleAndTextTuples],
    *,
    style: str = "",
    filter: FilterOrBool | None = None,
) -> AnyContainer:
    """Single-line text Window driven by `get_text` — the picker chrome rows
    (header, filter input, help text) all reduce to one of these."""
    win = Window(
        content=FormattedTextControl(text=get_text, show_cursor=False),
        height=1,
        always_hide_cursor=True,
        style=style,
    )
    if filter is None:
        return win
    return ConditionalContainer(content=win, filter=filter)
