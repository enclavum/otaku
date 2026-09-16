"""Rendering a play() stream: the spinner until the first signal, the
pinned status row for the whole window, dim thinking, prose through the
typesetter into the ledger's reply writer, the error and stats lines —
and the in-stream Ctrl+R watcher (raw-tty, POSIX; a no-op elsewhere).

Cancellation is the generator contract: Ctrl+C closes the stream and
returns to the prompt — the partial is recorded by the backend's own
close handling; Ctrl+R closes it and reports `regen` so the caller runs
`api.play.regenerate` and shows the fresh take in place.
"""

import contextlib
import os
import select
import signal
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, Self

from otaku.backend.api.play import Declined, Done, Failed, PlayEvent, Reasoning, Recorded, Text
from otaku.console.sound import ring
from otaku.formatting import printable
from otaku.terminal.chat.chat import Chat
from otaku.terminal.tty import DIM, RESET, error_line
from otaku.terminal.tty.render import message
from otaku.terminal.tty.spinner import Spinner
from otaku.terminal.tty.typography import Streamer

# POSIX-only raw-terminal control for the in-stream Ctrl+R watcher. Absent
# on Windows — the watcher degrades to a no-op there; Ctrl+C cancellation
# still goes through the kernel.
try:
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

_CTRL_R = b"\x12"


def show(chat: Chat, events: Iterator[PlayEvent]) -> bool:
    """Drive one stream onto the screen. True when Ctrl+R asked for a
    fresh take — the caller regenerates and calls back in; the erase (or
    the regenerating marker) is handled here. Ctrl+C closes the stream
    (the backend records the partial) and returns to the prompt.

    Everything shown inline — the echoed block, thinking, prose, error
    and stats lines — prints through the ledger, so the exchange's row
    count can never drift from the screen (the spinner and the pinned
    status row erase themselves and stay outside)."""
    out = chat.ledger.reply
    session = chat.session
    in_thinking = False
    thinking_nl = 0  # trailing newlines the thinking text itself printed
    streamed = False  # any prose shown yet (the error line's lead blank)
    chars = 0
    interrupted = False
    start = time.monotonic()

    spinner = Spinner()
    spinner.start()
    streamer = Streamer(out)
    watcher = _StreamWatcher()
    # The activity line survives the prompt's absence: entering `pinned`
    # reserves the bottom terminal row and paints the worker's status
    # there for the whole stream; leaving it releases the row.
    with chat.status_line.pinned(), watcher:
        try:
            for event in events:
                spinner.stop()  # idempotent — the first real signal clears it
                if isinstance(event, Recorded):
                    # The played turn echoes as the grey block; the reply
                    # streams under it. The wait resumes, so the spinner
                    # comes back until the first delta. The record's own
                    # note (a /roll's dice) prints dim under the block —
                    # the card import's report line is the family.
                    chat.ledger.echo_block(message(event.message.body, "user"))
                    if event.note:
                        out.write(f"{DIM}[ {event.note} ]{RESET}\n\n")
                    spinner.start()
                elif isinstance(event, Reasoning):
                    if not in_thinking:
                        out.write(DIM + "(thinking) ")
                        in_thinking = True
                        thinking_nl = 0
                    shown = printable(event.text)
                    if shown:
                        tail = len(shown) - len(shown.rstrip("\n"))
                        # A chunk of only newlines extends the run; any
                        # other chunk restarts it at its own tail.
                        thinking_nl = thinking_nl + tail if tail == len(shown) else tail
                    out.write(shown)
                    out.flush()
                elif isinstance(event, Text):
                    if in_thinking:
                        # Exactly one blank line between thinking and the
                        # prose, whatever the model's own trailing
                        # newlines: write only what is missing — models
                        # differ (none, one, two), and the screen must not.
                        out.write(RESET + "\n" * max(0, 2 - thinking_nl))
                        in_thinking = False
                    streamer.feed(event.text)
                    streamed = True
                    chars += len(event.text)
                elif isinstance(event, Declined):
                    out.write(event.reason + "\n")
                elif isinstance(event, Failed):
                    streamer.flush()
                    if in_thinking:
                        out.write(RESET)
                        in_thinking = False
                    # What streamed is already on the screen, so the story
                    # keeps it — exactly as a Ctrl+C does. A failure before
                    # any output starts at the margin — no stray blank.
                    lead = "\n" if streamed else ""
                    out.write(lead + error_line(f"[ error: {event.reason} ]") + "\n")
                elif isinstance(event, Done):
                    streamer.flush()
                    if in_thinking:
                        out.write(RESET)
                        in_thinking = False
                    if streamed:
                        out.write("\n")
                    if event.stats:
                        out.write(DIM + event.stats + RESET + "\n")
                    # A reply the model did not finish: the backend's
                    # sentence, in the stats line's family.
                    if event.report is not None and event.report.notice:
                        out.write(DIM + f"[ {event.report.notice} ]" + RESET + "\n")
        except KeyboardInterrupt:
            interrupted = True
        finally:
            spinner.stop()
            streamer.flush()
            # Cancel-and-keep: closing the generator makes the backend
            # record the partial; a plain iterator has nothing to close.
            close = getattr(events, "close", None)
            if callable(close):
                close()

    if interrupted:
        # The cut line is ended — the prose or the thinking stopped
        # mid-line. A wait cut before anything showed has no line to end:
        # the echo's blank stands as the gap, and a newline here would be
        # a second blank, with the loop's gap making a third.
        cut_midline = streamed or in_thinking
        if in_thinking:
            out.write(RESET)
            in_thinking = False
        if cut_midline:
            out.write("\n")
        if session.verbose:
            elapsed = time.monotonic() - start
            rate = chars / elapsed if elapsed > 0 else 0.0
            out.write(
                f"{DIM}[ total {elapsed:.1f}s, "
                f"eval {chars} chars @ {rate:.0f} chars/s, interrupted ]{RESET}\n"
            )

    if watcher.regen_requested:
        # Ctrl+R during the stream: the partial is recorded like any
        # reply; the caller's regenerate siblings it away — and off the
        # screen too, the fresh reply streaming in its place. A partial
        # the ledger cannot erase gets the marker under the break rule
        # instead, which ends the clearable run like any command output.
        if not chat.ledger.erase_reply_tail():
            chat.ledger.invalidate()
            out.write("\n")
            chat.ledger.rule()
            out.write(f"{DIM}[ regenerating ]{RESET}\n\n")
        return True
    if session.notification and not interrupted:
        # The turn is over and the screen wants its reader back. Not
        # after a Ctrl+C: whoever pressed it is already here.
        ring(session.terminal.notification_sound)
    return False


class _StreamWatcher:
    """While streaming, put the TTY in cbreak mode and watch for Ctrl+R.
    On Ctrl+R, set `regen_requested` and raise SIGINT so the main thread's
    blocking stream read interrupts. Ctrl+C still works through the kernel
    (ISIG stays on under cbreak). On exit, pending input is discarded so it
    cannot leak into the next prompt. No-op when stdin isn't a TTY."""

    def __init__(self) -> None:
        self.regen_requested = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._orig: Any | None = None
        self._fd: int = -1

    def __enter__(self) -> Self:
        if termios is None or tty is None:
            return self
        try:
            fd = sys.stdin.fileno()
        except (ValueError, OSError):
            return self
        if not os.isatty(fd):
            return self
        try:
            self._orig = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except termios.error:
            return self
        self._fd = fd
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        if self._fd >= 0:
            with contextlib.suppress(termios.error, OSError):
                termios.tcflush(self._fd, termios.TCIFLUSH)
        if self._orig is not None:
            with contextlib.suppress(termios.error):
                termios.tcsetattr(self._fd, termios.TCSANOW, self._orig)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.05)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                data = os.read(self._fd, 1)
            except OSError:
                return
            if not data:
                return
            if data == _CTRL_R:
                self.regen_requested = True
                os.kill(os.getpid(), signal.SIGINT)
                return
