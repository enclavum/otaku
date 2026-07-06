"""Braille spinner shown while waiting on the model.

start()/stop() are idempotent so callers can stop unconditionally on every
chunk path without bookkeeping.
"""

from __future__ import annotations

import sys
import threading

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
INTERVAL = 0.1


class Spinner:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        self._thread = None
        # Erase the spinner row so streaming output starts at column 0.
        sys.stdout.write("\r\x1b[2K")
        sys.stdout.flush()

    def _run(self) -> None:
        i = 0
        suffix = f" {self._label}" if self._label else ""
        while not self._stop.wait(INTERVAL):
            sys.stdout.write(f"\r{FRAMES[i % len(FRAMES)]}{suffix} ")
            sys.stdout.flush()
            i += 1
