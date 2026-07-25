"""Shared widgets and helpers for the full-screen prompt_toolkit apps.

`bordered_box` builds the chrome the pickers use for modal dialogs,
preview panes, and confirmation overlays. The visual difference between
"dialog" and "preview" is just the inner-padding amount and the styles the
caller passes — the box geometry is identical.
"""

import textwrap
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
from prompt_toolkit.layout.controls import FormattedTextControl, UIControl
from prompt_toolkit.layout.dimension import AnyDimension

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

# Confirm-dialog answers without switching layouts (Latin + Cyrillic keys).
KEY_YES = {"y", "н"}
KEY_NO = {"n", "т"}


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
