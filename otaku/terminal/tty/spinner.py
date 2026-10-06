"""Braille spinner shown while waiting on the model.

start()/stop() are idempotent so callers can stop unconditionally on every
chunk path without bookkeeping. The frame is drawn where the cursor
stands, with the cursor left one blank cell AFTER it — a block cursor
over the frame would show it inverted, and touching it reads as part of
it — and erased on stop, the cursor back where the wait began: on an
empty row under a block, mid-line on the canvas right after the text
the reply will go on from, so what streams next starts exactly there.
"""

import sys
import threading

from otaku.terminal.tty import ERASE_TO_END

# The 6-dot set: light enough to sit beside regular text.
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_INTERVAL = 0.1


class Spinner:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._drawn = False  # a frame stands on screen, to back over

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop = threading.Event()  # fresh gate: a stopped spinner restarts
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        self._thread = None
        # Back over the frame and its blank cell and erase them, so
        # streaming output starts where the wait began.
        if self._drawn:
            sys.stdout.write(f"\b\b{ERASE_TO_END}")
            sys.stdout.flush()
        self._drawn = False

    def _run(self) -> None:
        # Two cells, the frame and a blank: the first frame is written
        # where the cursor stands (wrapping to the next row where the row
        # was full, as the text would), every later one over it, two
        # backspaces first; the cursor rests after the blank throughout.
        i = 0
        while not self._stop.wait(_INTERVAL):
            back = "\b\b" if self._drawn else ""
            sys.stdout.write(f"{back}{FRAMES[i % len(FRAMES)]} ")
            sys.stdout.flush()
            self._drawn = True
            i += 1
