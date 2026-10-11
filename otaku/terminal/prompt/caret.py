"""The cursor the prompt shows (`Caret`), and the caret's colour painted
while it stands (`caret_painter`): in story mode an empty line is where
the story goes on."""

import atexit
import sys
from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.cursor_shapes import CursorShape, CursorShapeConfig
from prompt_toolkit.output.vt100 import Vt100_Output

from otaku.terminal.prompt.lines import Carry
from otaku.terminal.tty import CURSOR_COLOR, CURSOR_COLOR_RESET
from otaku.terminal.tty.theme import theme


class Caret(CursorShapeConfig):
    """The cursor the prompt shows: in story mode an empty line is where
    the story goes on, said by a blinking block in the caret's colour
    (`caret_painter`), as the page draws its caret on an empty canvas
    tail; a blinking beam once anything is typed, and everywhere else."""

    def __init__(self, carry: Carry) -> None:
        self._carry = carry

    def get_cursor_shape(self, application: Application[Any]) -> CursorShape:
        if _caret_shown(self._carry, application):
            return CursorShape.BLINKING_BLOCK
        return CursorShape.BLINKING_BEAM


def _caret_shown(carry: Carry, app: Application[Any]) -> bool:
    """Whether the story-mode caret stands: the canvas's keys in force and
    nothing typed."""
    return carry.canvas_keys and not app.current_buffer.text


def caret_painter(carry: Carry) -> Callable[[Application[Any]], None]:
    """After each frame: the caret's block painted in the theme's colour
    (OSC 12) while it stands, the terminal's own colour handed back
    (OSC 112) once anything is typed and as the prompt ends — on whatever
    output the session draws on, a VT one alone. The colour is restored at
    exit too, should the process end with it painted."""
    painted = False

    def reset() -> None:
        terminal = sys.__stdout__
        if terminal is not None:
            terminal.write(CURSOR_COLOR_RESET)
            terminal.flush()

    def paint(app: Application[Any]) -> None:
        nonlocal painted
        wanted = _caret_shown(carry, app) and not app.is_done
        caret = theme().caret.style
        if (
            wanted == painted
            or not isinstance(app.output, Vt100_Output)
            or not caret.startswith("#")
        ):
            return
        if wanted:
            app.output.write_raw(CURSOR_COLOR.format(f"rgb:{caret[1:3]}/{caret[3:5]}/{caret[5:7]}"))
            atexit.register(reset)
        else:
            app.output.write_raw(CURSOR_COLOR_RESET)
            atexit.unregister(reset)
        app.output.flush()
        painted = wanted

    return paint
