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
import re
import select
import signal
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, Self

from otaku.backend.api.play import (
    AssistantReasoning,
    AssistantText,
    AssistantToolCall,
    AssistantTurn,
    Declined,
    Done,
    Failed,
    PlayEvent,
    ToolTurn,
    UserTurn,
)
from otaku.backend.tools import TOOLS, Actor, get_tool_label
from otaku.console.sound import ring
from otaku.terminal.chat.chat import Chat
from otaku.terminal.tty import DIM, RESET, error_line
from otaku.terminal.tty.cursor import terminal_width
from otaku.terminal.tty.render import BlockStream, QuestionStream, message, spaced
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
    # The canvas: the one text — no block for the reader's line, the
    # reply going on where the text ended (`ledger.echo_flow`).
    is_canvas = session.layout.is_canvas
    # The text the reply goes on from, for its seam (`before`): the last
    # row's, until a played line lands and is the text instead.
    before = session.messages[-1].body if session.messages else ""
    prose_begun = False  # the reply's first prose piece places its seam
    streamed = False  # anything shown yet (the error line's lead blank, the line to end)
    chars = 0
    interrupted = False
    call_open = False  # inside a tool call, its opening tag already printed
    fed = ""  # all drawn so far, for the blank lines around a call's block
    after_call = False  # a call's block was drawn last: prose going on is set apart
    # The block being streamed — the thinking, a question, a note — and
    # which, until its end: the thinking's is the first piece of anything
    # else, a call's its closing fence.
    block: BlockStream | None = None
    block_name = ""
    shown = chat.shown_tools  # a story's switches, read once: they cannot move mid-stream
    start = time.monotonic()

    def close_block() -> None:
        """The block ended after its last piece — its closing fence, the
        first piece of what follows the thinking, or the stream's end
        inside it: the dim off, a last word or line placed, so what
        follows stands clear of it."""
        nonlocal fed, after_call, block, block_name, streamed
        if block is None:
            return
        written = block.close()
        fed += written
        streamed = streamed or bool(written)
        block, block_name = None, ""
        after_call = True

    # A continue: no line of the reader's to echo, so the reply goes on
    # where the text ended — placed NOW, before the wait, so the spinner
    # spins there as it does after an echoed line; a stream that then
    # declines or fails before a word says so on a row of its own
    # (`placed`), not mid-line.
    placed = False
    if is_canvas and chat.continuing and not chat.ledger.flow_open:
        chat.ledger.echo_flow(inline=True)
        chat.ledger.reply_begins()
        fed = chat.ledger.tail  # what a block's spacing is measured against
        placed = True
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
                # No thinking in the canvas: the one text has no place for
                # it, and it is never stored — the spinner turns on through it.
                if is_canvas and isinstance(event, AssistantReasoning):
                    continue
                spinner.stop()  # idempotent — the first real signal clears it
                if isinstance(event, UserTurn):
                    if is_canvas:
                        # The canvas: the played line is prose in the one
                        # text, no block. The ledger places the cursor by the
                        # line's seam (the event's `join_type`: under the standing
                        # blank, or on where the text ended) and the line is
                        # typeset through the reply's own streamer, so a quote
                        # it opens and the reply closes reads as one.
                        inline = chat.ledger.echo_flow(inline=event.join_type.value == "inline")
                        # PROTOTYPE: a paragraph's lead and a break the line
                        # ends on are the joins' to draw, not drawn twice; a
                        # line placed on a row of its own (under a command's
                        # output) drops the seam's space it goes on with
                        body = event.message.body.rstrip("\n")
                        if not inline:
                            body = re.sub(r"^\s*\n", "", body).lstrip(" ")
                        streamer.feed(body)
                        streamer.flush()
                        chat.ledger.reply_begins()
                        fed += event.message.body
                        before = event.message.body
                        streamed = True
                    else:
                        # The played turn echoes as the grey block; the reply
                        # streams under it. The wait resumes, so the spinner
                        # comes back until the first delta. The record's own
                        # note (a /roll's dice) prints dim under the block —
                        # the card import's report line is the family.
                        chat.ledger.echo_block(
                            message(
                                event.message.body, "user", pictures=len(event.message.attachments)
                            )
                        )
                    if event.note:
                        out.write(f"{DIM}[ {event.note} ]{RESET}\n\n")
                    spinner.start()
                elif isinstance(event, AssistantReasoning):
                    # The thinking streams as a dim block behind the bar
                    # (`render`), as a note does.
                    if block is None or block_name != "thinking":
                        close_block()
                        block = BlockStream(out, terminal_width(), dim=True)
                        block_name = "thinking"
                        fed += block.open(streamer, fed)
                    written = block.feed(event.text)
                    fed += written
                    streamed = streamed or bool(written)
                elif isinstance(event, AssistantText):
                    if block_name == "thinking":
                        close_block()  # one blank line to the prose, as after any block
                    text = event.text
                    # The seam, as the backend reads it when the row lands
                    # (`assembler.join_of`, the rule copied as the page's
                    # `seamJoin`): a flush first piece after a text that does
                    # not end in a space opens a paragraph, and the model
                    # wrote none of the break — so the drawer does.
                    flush = not text[:1].isspace() and not before.endswith(" ")
                    if is_canvas and not prose_begun and not after_call and flush:
                        text = "\n\n" + text
                    prose_begun = True
                    if after_call:
                        if not text.strip():
                            continue  # the model's newlines after a block: drawn once, below
                        text = spaced(fed) + text.lstrip("\n")
                        after_call = False
                    streamer.feed(text)
                    fed += text
                    streamed = True
                    chars += len(event.text)
                elif isinstance(event, AssistantToolCall):
                    if block_name == "thinking":
                        close_block()
                    tool = TOOLS.get(event.name)
                    if tool is not None and tool.actor is Actor.SYSTEM:
                        # A call otaku runs — the search's query — is the
                        # model's, not the story's: nothing drawn, as
                        # `render.message` draws nothing for it; the result
                        # follows as a block of its own (`ToolTurn`).
                        after_call = True
                        continue
                    if event.name in ("question", "note"):
                        if event.name not in shown:
                            # A tool the story does not show — the notes' display
                            # off, or the tool itself — and the prose goes on as
                            # after any block, so no hole marks the place.
                            after_call = True
                            continue
                        # Streamed as it arrives, a block behind the bar
                        # (`render`): the question alone of a question, its
                        # options parting from it line by line; a note dim.
                        if block is None:
                            width = terminal_width()
                            if event.name == "question":
                                block = QuestionStream(out, width)
                            else:
                                block = BlockStream(out, width, dim=True)
                            block_name = event.name
                            fed += block.open(streamer, fed)
                        written = block.feed(event.text)
                        fed += written
                        streamed = streamed or bool(written)
                        if event.closed:
                            close_block()
                        continue
                    # Every other call prints as it streamed, fences and
                    # all, until its look is decided: the opening fence
                    # ahead of its first piece, the closing fence after the
                    # piece that closed it — the language's canonical form
                    # (`context.tool_calls`), which the terminal may not
                    # import, so spelled here as `render.message` spells it.
                    opening = "" if call_open else f"```otk-{event.name}\n"
                    closing = "\n```" if event.closed else ""
                    call_open = not event.closed
                    streamer.feed(opening + event.text + closing)
                    fed += opening + event.text + closing
                    streamed = True
                elif isinstance(event, AssistantTurn):
                    # The row as recorded is what the screen already shows,
                    # drawn as it streamed: nothing to draw.
                    continue
                elif isinstance(event, ToolTurn):
                    # The reply holding the call ended at it; the tool turn
                    # is drawn as its own dim block behind the bar, the label
                    # alone — the result is the model's to read, not the
                    # story's — as `render.message` draws it again on a
                    # resume; the reply that continues streams under it, set
                    # apart as after any block.
                    close_block()
                    call_open = False
                    block = BlockStream(out, terminal_width(), dim=True)
                    block_name = "tool"
                    fed += block.open(streamer, fed)
                    label = get_tool_label(event.message.provider, event.message.body)
                    written = block.feed(label or "Tool")
                    fed += written
                    streamed = streamed or bool(written)
                    close_block()
                elif isinstance(event, Declined):
                    lead = "\n" if placed and not streamed else ""
                    out.write(lead + event.reason + "\n")
                elif isinstance(event, Failed):
                    close_block()
                    streamer.flush()
                    # What streamed is already on the screen, so the story
                    # keeps it — exactly as a Ctrl+C does. A failure before
                    # any output starts at the margin — no stray blank —
                    # unless the cursor was placed mid-line for a continue.
                    lead = "\n" if streamed or placed else ""
                    out.write(lead + error_line(f"[ error: {event.reason} ]") + "\n")
                elif isinstance(event, Done):
                    close_block()  # a reply cut inside its call still shows it
                    streamer.flush()
                    if streamed:
                        out.write("\n")
                    if event.stats:
                        out.write(DIM + event.stats + RESET + "\n")
        except KeyboardInterrupt:
            interrupted = True
        finally:
            spinner.stop()
            close_block()  # a ^C inside a block: its end drawn before the prompt
            streamer.flush()
            # Cancel-and-keep: closing the generator makes the backend
            # record the partial; a plain iterator has nothing to close.
            close = getattr(events, "close", None)
            if callable(close):
                close()

    if interrupted:
        # The cut line is ended — the prose, a block or the thinking
        # stopped mid-line. A wait cut before anything showed has no line
        # to end: the echo's blank stands as the gap, and a newline here
        # would be a second blank, with the loop's gap making a third.
        if streamed:
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
            if is_canvas:
                # the fresh take is a paragraph under the marker, placed
                # here so the next stream does not place it again
                chat.ledger.echo_flow(inline=False)
                chat.ledger.reply_begins()
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
