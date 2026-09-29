"""Braille spinner shown while waiting on the model.

start()/stop() are idempotent so callers can stop unconditionally on every
chunk path without bookkeeping. The frame is drawn where the cursor
stands, with the cursor left AFTER it — a block cursor over the frame
would show it inverted — and erased on stop, the cursor back where the
wait began: on an empty row in chat mode, mid-line in story mode right
after the text the reply will go on from, so what streams next starts
exactly there.
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
        # Back over the frame and erase it, so streaming output starts
        # where the wait began.
        if self._drawn:
            sys.stdout.write(f"\b{ERASE_TO_END}")
            sys.stdout.flush()
        self._drawn = False

    def _run(self) -> None:
        # One cell: the first frame is written where the cursor stands
        # (wrapping to the next row where the row was full, as the text
        # would), every later one over it, a backspace first; the cursor
        # rests after the frame throughout.
        i = 0
        while not self._stop.wait(_INTERVAL):
            back = "\b" if self._drawn else ""
            sys.stdout.write(f"{back}{FRAMES[i % len(FRAMES)]}")
            sys.stdout.flush()
            self._drawn = True
            i += 1
