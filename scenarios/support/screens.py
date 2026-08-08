"""Driving the tui surfaces headless: the real prompt_toolkit Application
runs with scripted keys queued on a pipe input."""

import threading
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
RIGHT = "\x1b[C"
DELETE = "\x1b[3~"
CTRL_S = "\x13"
CTRL_V = "\x16"


def pasted(text: str) -> str:
    """`text` as a terminal paste — the bracketed-paste sequence Cmd+V
    (and, on many terminals, Ctrl+V) sends instead of a control byte."""
    return f"\x1b[200~{text}\x1b[201~"


def run_screen(keys: str, surface: Callable[[], Any], *, patience: float = 2.0) -> Any:
    """`surface()` with `keys` queued as its terminal input. A watchdog
    closes the pipe `patience` seconds in, so a surface that outlives its
    keys (a key swallowed by a busy modal, say) reads EOF and dies with
    an exception instead of hanging the suite."""
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        watchdog = threading.Timer(patience, pipe.close)
        watchdog.start()
        try:
            with create_app_session(input=pipe, output=DummyOutput()):
                return surface()
        finally:
            watchdog.cancel()
