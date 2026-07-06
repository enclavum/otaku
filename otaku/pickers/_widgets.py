"""Shared widgets and helpers for the prompt_toolkit pickers.

`bordered_box` builds the chrome both pickers use for modal dialogs,
preview panes, and confirmation overlays. The visual difference between
"dialog" and "preview" is just the inner-padding amount and the styles
the caller passes — the box geometry is identical.
"""

from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import FilterOrBool
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.containers import (
    AnyContainer,
    ConditionalContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import AnyDimension

# Style entries shared between both pickers. Each picker merges its own
# row / preview / dialog-error overrides on top of this dict.
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

# answer without switching layouts. Shared by both pickers.
KEY_YES = {"y", "н"}
KEY_NO = {"n", "т"}


def page_step(reserved: int = 5) -> int:
    """Rows to move for PageUp/PageDown: a screenful minus `reserved` chrome rows."""
    return max(1, get_app().output.get_size().rows - reserved)


def bordered_box(
    control: FormattedTextControl,
    *,
    width: AnyDimension = None,
    height: AnyDimension = None,
    style: str = "",
    border_style: str = "class:frame.border",
    inner_pad: int = 2,
    wrap: bool = True,
) -> AnyContainer:
    """Box with sharp single-line corners (┌─┐│└┘), `inner_pad`-col side
    padding, and one blank row top and bottom inside the border.

    Used for modal dialogs (`width=D(...,...)`+`height=D.exact(...)`,
    `wrap=False`) and full-height preview panes (`width=D(weight=1)`,
    `wrap=True`).
    """
    middle = VSplit(
        [
            Window(width=inner_pad, char=" ", style=style, always_hide_cursor=True),
            Window(content=control, wrap_lines=wrap, always_hide_cursor=True, style=style),
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
    """Single-line text Window driven by `get_text`. Wrapped in a
    ConditionalContainer when `filter` is supplied — the picker chrome
    rows (header, filter input, help text) all reduce to one of these.
    """
    win = Window(
        content=FormattedTextControl(text=get_text, show_cursor=False),
        height=1,
        always_hide_cursor=True,
        style=style,
    )
    if filter is None:
        return win
    return ConditionalContainer(content=win, filter=filter)
