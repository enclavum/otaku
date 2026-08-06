"""Braille spinner shown while waiting on the model.

start()/stop() are idempotent so callers can stop unconditionally on every
chunk path without bookkeeping.
"""

import sys
import threading

from otaku.terminal import ERASE_LINE

# The 6-dot set: light enough to sit beside regular text.
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_INTERVAL = 0.1


class Spinner:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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
        # Erase the spinner row so streaming output starts at column 0.
        sys.stdout.write(f"\r{ERASE_LINE}")
        sys.stdout.flush()

    def _run(self) -> None:
        i = 0
        while not self._stop.wait(_INTERVAL):
            sys.stdout.write(f"\r{FRAMES[i % len(FRAMES)]} ")
            sys.stdout.flush()
            i += 1
