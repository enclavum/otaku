"""Driving the tui surfaces headless: the real prompt_toolkit Application
runs with scripted keys queued on a pipe input."""

from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

ENTER = "\r"
ESC = "\x1b"
TAB = "\t"
UP = "\x1b[A"
DOWN = "\x1b[B"
DELETE = "\x1b[3~"
CTRL_S = "\x13"


def run_screen(keys: str, surface: Callable[[], Any]) -> Any:
    """`surface()` with `keys` queued as its terminal input. The pipe ends
    after the script, so a surface that outlives its keys reads EOF and
    dies with an exception instead of hanging the suite."""
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        with create_app_session(input=pipe, output=DummyOutput()):
            return surface()
