"""Rendering a play() stream: the spinner until the first signal, the
pinned status row for the whole window, dim thinking, prose through the
typesetter into the ledger's reply writer, the error and stats lines —
and the in-stream Ctrl+R watcher (raw-tty, POSIX; a no-op elsewhere).

Cancellation is the generator contract: Ctrl+C closes the stream and
returns to the prompt — the partial is recorded by the backend's own
close handling; Ctrl+R closes it and reports `regen` so the caller runs
`api.stories.regens` and shows the fresh take in place.
"""

import contextlib
import io
import os
import select
import signal
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, Self, TextIO

from otaku.console.sound import ring
from otaku.formatting import format_duration
from otaku.terminal.api import (
    Actor,
    AssistantMessage,
    AssistantReasoning,
    AssistantText,
    AssistantToolCall,
    Declined,
    Done,
    EmptyReply,
    Failed,
    JoinType,
    Message,
    PlayEvent,
    StoryMode,
    ToolMessage,
    UserMessage,
)
from otaku.terminal.chat.chat import Chat
from otaku.terminal.tty import DIM, ERASE_TO_END, RESET, error_line
from otaku.terminal.tty.cursor import terminal_width
from otaku.terminal.tty.render import (
    BlockStream,
    QuestionStream,
    flow,
    line_text,
    message,
    reply_start_index,
    spaced,
    typeset_line,
)
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
# A folded thinking's label (`/set show_thinking off`) while it goes on,
# and once it ended, with how long it took. The page's twin:
# play/transcript.js.
_THINKING = "Thinking…"
_THOUGHT = "Thought for {}"


@contextlib.contextmanager
def waiting() -> Iterator[None]:
    """The spinner while a reply is asked for: accepting the line may
    itself wait, behind a reply another frontend is streaming. A call
    answered at once draws nothing (`Spinner` draws after its first
    interval)."""
    spinner = Spinner()
    spinner.start()
    try:
        yield
    finally:
        spinner.stop()


def show(chat: Chat, events: Iterator[PlayEvent]) -> bool:
    """Drive one stream onto the screen. True when Ctrl+R asked for a
    fresh take — the caller regenerates and calls back in; the erase (or
    the regenerating marker) is handled here. Ctrl+C asks the reply to
    stop, what the backend kept is drawn to its last piece, and the
    prompt comes back. Marks the submission as one that streamed a reply
    (`Chat.replied`).

    Everything shown inline — the echoed block, thinking, prose, error
    and stats lines — prints through the ledger, so the exchange's row
    count can never drift from the screen (the spinner and the pinned
    status row erase themselves and stay outside)."""
    chat.replied = True
    out = chat.ledger.reply
    # The canvas: the one text — no block for the reader's line, the
    # reply going on where the text ended (`ledger.echo_flow`).
    is_story_mode = chat.layout.mode is StoryMode.STORY
    streamed = False  # anything shown yet (the error line's lead blank, the line to end)
    in_text = True  # the story's text drawn last, not a block: where a row may stay open
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
    show_thinking = chat.api.settings.terminal.get().show_thinking  # likewise
    thought_at: float | None = None  # when a folded thinking's label went up, until settled
    call_label = ""  # the label of a call otaku runs, standing until its turn lands
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

    def put_label(text: str) -> None:
        """A block of a label alone — a folded thinking's, a call otaku
        runs — dim behind the bar, a space after it so the cursor does not
        touch it: the last thing drawn until `relabel` rewrites it."""
        nonlocal fed, block, in_text, streamed
        spinner.stop()
        close_block()
        block = BlockStream(out, terminal_width(), dim=True)
        fed += block.open(streamer, fed)
        in_text = False
        written = block.feed(text)
        fed += written
        streamed = streamed or bool(written)
        close_block()
        out.write(" ")
        out.flush()

    def relabel(text: str) -> None:
        """The label `put_label` drew, rewritten in place once what it
        stands for is over — still the last thing drawn, the cursor on its
        row — in one write, over the old one, so the row never stands
        blank, and a space after it as `put_label` leaves. Off a terminal
        it stays as drawn."""
        if not sys.stdout.isatty():
            return
        drawn = io.StringIO()
        label = BlockStream(drawn, terminal_width(), dim=True)
        label.begin()
        label.feed(text)
        label.close()
        out.write(f"\r{drawn.getvalue()} {ERASE_TO_END}")
        out.flush()

    def settle() -> None:
        """A folded thinking's label, its thinking over: how long it took."""
        nonlocal thought_at
        if thought_at is not None:
            relabel(_THOUGHT.format(format_duration(max(1.0, time.monotonic() - thought_at))))
            thought_at = None

    # A continue: no line of the reader's to echo, so the reply goes on
    # where the text ended — placed NOW, before the wait, so the spinner
    # spins there as it does after an echoed line; a stream that then
    # declines or fails before a word says so on a row of its own
    # (`placed`), not mid-line.
    placed = False
    if is_story_mode and chat.continuing and not chat.ledger.flow_open:
        chat.ledger.echo_flow(inline=True)
        chat.ledger.reply_begins()
        fed = chat.ledger.tail  # what a block's spacing is measured against
        placed = True
    elif is_story_mode:
        # a regenerate's take goes on where the reply it replaces began,
        # mid-row after the reader's words: placed there by its erase
        placed = chat.ledger.mid_row
    spinner = Spinner()
    spinner.start()
    streamer = Streamer(out)
    watcher = _StreamWatcher()
    # The activity line survives the prompt's absence: entering `pinned`
    # reserves the bottom terminal row and paints the worker's status
    # there for the whole stream, and on until the next prompt takes the
    # row over.
    # A failure or a refusal in story mode ends the row it broke off,
    # after every last write of the stream's own — a style reset among
    # them — has landed.
    end_row = False
    # Ctrl+C asks the reply to stop instead of tearing through the drawing:
    # the stream then ends with what the backend kept, every event of it
    # drawn, so the screen holds what the story does. The first hands
    # SIGINT back, so a second is the ordinary interrupt.
    stop_asked = False
    finished = False  # the reply's end came: a Ctrl+C after it cut nothing
    is_main = threading.current_thread() is threading.main_thread()
    previous_handler = signal.getsignal(signal.SIGINT) or signal.default_int_handler

    def ask_to_stop(*_: object) -> None:
        nonlocal stop_asked
        signal.signal(signal.SIGINT, previous_handler)
        cancel = getattr(events, "cancel", None)
        if not callable(cancel):
            raise KeyboardInterrupt  # a plain iterator: nothing to ask
        stop_asked = True
        cancel()

    with chat.status_line.pinned(), watcher:
        if is_main:
            signal.signal(signal.SIGINT, ask_to_stop)
        try:
            for event in events:
                chat.status_line.paint_held()  # the last event drawn may have moved off the edge
                # The status row says what the reply is doing while its
                # pieces stream — thinking, a call of any tool, by its name —
                # where the transcript draws nothing for it: story mode's one
                # text, a call whose block is hidden. A call otaku runs keeps
                # it while the tool works, after the reply holding the call
                # landed; gone with whatever comes next.
                if isinstance(event, AssistantReasoning):
                    chat.status_line.set_note("thinking" if is_story_mode else "")
                elif isinstance(event, AssistantToolCall):
                    is_drawn = (
                        event.name in shown and (event.name == "question" or not is_story_mode)
                        if event.name in ("question", "note")
                        else not is_story_mode
                    )
                    chat.status_line.set_note(
                        "" if is_drawn else (event.label or event.name).lower()
                    )
                elif not isinstance(event, AssistantMessage):
                    chat.status_line.set_note("")
                # No thinking in the canvas: the one text has no place for
                # it, and it is never stored — the spinner turns on through it.
                if is_story_mode and isinstance(event, AssistantReasoning):
                    continue
                if isinstance(event, AssistantReasoning) and not show_thinking:
                    # Folded: drawn as a tool turn is, the label alone, once
                    # a run, a space after it so the cursor does not touch
                    # it; the status row says the thinking goes on.
                    if thought_at is None:
                        put_label(_THINKING)
                        thought_at = time.monotonic()
                    continue
                spinner.stop()  # idempotent — the first real signal clears it
                settle()
                if call_label and not isinstance(
                    event, (AssistantToolCall, AssistantMessage, ToolMessage)
                ):
                    relabel(call_label)  # cut before its tool ran: the label as it is
                    call_label = ""
                if isinstance(event, UserMessage):
                    if is_story_mode:
                        # The canvas: the played line is prose in the one
                        # text, no block. The ledger places the cursor by the
                        # line's join (the turn's `join_type`: under the
                        # standing blank, or on where the text ended, one
                        # space after it) and the line is typeset through the
                        # reply's own streamer, so a quote it opens and the
                        # reply closes reads as one.
                        inline = chat.ledger.echo_flow(
                            inline=event.message.join_type is JoinType.INLINE
                        )
                        # the breaks at its edges are the joins' to draw, not
                        # drawn twice; a chapter's words are its heading
                        body = line_text(event.message).strip("\n")
                        if inline:
                            streamer.feed(" ")
                        typeset_line(streamer, body)
                        streamer.flush()
                        chat.ledger.reply_begins()
                        fed += line_text(event.message)
                        streamed = True
                    else:
                        # The played turn echoes as the grey block; the reply
                        # streams under it. The wait resumes, so the spinner
                        # comes back until the first delta. The record's own
                        # note (a /roll's dice) prints dim under the block —
                        # the card import's report line is the family.
                        chat.ledger.echo_block(
                            message(
                                event.message.body,
                                "user",
                                segments=event.message.segments,
                                pictures=len(event.message.attachments),
                            )
                        )
                    # not in story mode's one text, which the reply goes on in
                    if event.note and not is_story_mode:
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
                        in_text = False
                    written = block.feed(event.text)
                    fed += written
                    streamed = streamed or bool(written)
                elif isinstance(event, AssistantText):
                    if block_name == "thinking":
                        close_block()  # one blank line to the prose, as after any block
                    text = event.text
                    # The reply's first piece says how it joins the text
                    # before it (`join_type`): one space, or a paragraph —
                    # its own breaks the join's to draw.
                    join = event.join_type.value if event.join_type is not None else None
                    if is_story_mode and join == "inline":
                        text = " " + text
                    elif is_story_mode and join == "paragraph":
                        text = "\n\n" + text.lstrip("\n")
                    if is_story_mode and join is not None:
                        # a reply's first piece is set apart by its join
                        # alone — not by a block the reply before ended on,
                        # which story mode does not draw
                        after_call = False
                    if after_call:
                        if not text.strip():
                            continue  # the model's newlines after a block: drawn once, below
                        text = spaced(fed) + text.lstrip("\n")
                        after_call = False
                    streamer.feed(text)
                    in_text = True
                    fed += text
                    streamed = True
                    chars += len(event.text)
                elif isinstance(event, AssistantToolCall):
                    if block_name == "thinking":
                        close_block()
                    if is_story_mode and event.name != "question":
                        # story mode's text draws no tool's block but the
                        # question it is asked (`render.flow`)
                        after_call = True
                        continue
                    if event.actor is Actor.SYSTEM:
                        # A call otaku runs — the search's query — is the
                        # model's, not the story's: the backend's sentence for
                        # it while it runs, the line's first word capitalized,
                        # rewritten to its turn's label as that lands
                        # (`ToolMessage`).
                        if not call_label:
                            call_label = event.label
                            put_label(f"{event.status[:1].upper()}{event.status[1:]}…")
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
                            if event.name == "question" and is_story_mode:
                                # where the prose ends, for the answer to take
                                # the question back to (`drop_question`)
                                streamer.flush()
                                chat.ledger.question_begins()
                            if event.name == "question":
                                block = QuestionStream(out, width)
                            else:
                                block = BlockStream(out, width, dim=True)
                            block_name = event.name
                            fed += block.open(streamer, fed)
                            in_text = False
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
                    # (`context.tools`), which the terminal may not
                    # import, so spelled here as `render.message` spells it.
                    opening = "" if call_open else f"```otk-{event.name}\n"
                    closing = "\n```" if event.closed else ""
                    call_open = not event.closed
                    streamer.feed(opening + event.text + closing)
                    fed += opening + event.text + closing
                    streamed = True
                elif isinstance(event, AssistantMessage):
                    # The row as recorded is what the screen already shows,
                    # drawn as it streamed: nothing to draw.
                    continue
                elif isinstance(event, ToolMessage):
                    # The reply holding the call ended at it; the call's label
                    # becomes the tool turn's, a dim block of the label alone
                    # — the result is the model's to read, not the story's —
                    # as `render.message` draws it again on a resume; the
                    # reply that continues streams under it, set apart as
                    # after any block. Story mode's text draws none: the
                    # reply that continues goes on as its join says, in the
                    # paragraph the call interrupted (`render.flow`).
                    if is_story_mode:
                        continue
                    call_open = False
                    if call_label:
                        relabel(event.message.tool_label or "")
                        call_label = ""
                    else:
                        put_label(event.message.tool_label or "")
                elif isinstance(event, Declined):
                    if is_story_mode:
                        # on the status row, as a failure is: the text whole
                        chat.status_note = event.reason
                        end_row = streamed or placed
                    else:
                        lead = "\n" if placed and not streamed else ""
                        out.write(lead + event.reason + "\n")
                elif isinstance(event, EmptyReply):
                    # nothing to keep: the sentence set as the terminal's dim
                    # report, lowercase in its brackets as the stats line is
                    # — on the status row in story mode, as the stats are
                    report = event.note.rstrip(".")
                    report = report[:1].lower() + report[1:]
                    if is_story_mode:
                        chat.status_note = report
                        end_row = streamed or placed
                    else:
                        lead = "\n" if placed and not streamed else ""
                        out.write(f"{lead}{DIM}[ {report} ]{RESET}\n")
                elif isinstance(event, Failed):
                    close_block()
                    streamer.flush()
                    # What streamed is already on the screen, so the story
                    # keeps it — exactly as a Ctrl+C does. A failure before
                    # any output starts at the margin — no stray blank —
                    # unless the cursor was placed mid-line for a continue.
                    lead = "\n" if streamed or placed else ""
                    if is_story_mode:
                        # story mode's text takes no line of the medium's:
                        # the failure stands on the status row, the row it
                        # broke off ended as a reply's would be
                        chat.status_note = f"error: {event.reason}"
                        end_row = bool(lead)
                    else:
                        out.write(lead + error_line(f"[ error: {event.reason} ]") + "\n")
                elif isinstance(event, Done):
                    finished = True
                    close_block()  # a reply cut inside its call still shows it
                    streamer.flush()
                    if streamed:
                        _end_row(chat, out, story_mode=is_story_mode and in_text)
                    if event.stats and is_story_mode:
                        chat.status_note = _unbracketed(
                            event.stats
                        )  # on the status row, the text whole
                    elif event.stats:
                        out.write(DIM + event.stats + RESET + "\n")
        except KeyboardInterrupt:
            interrupted = True
        finally:
            if is_main and signal.getsignal(signal.SIGINT) is ask_to_stop:
                signal.signal(signal.SIGINT, previous_handler)
            spinner.stop()
            close_block()  # a ^C inside a block: its end drawn before the prompt
            settle()  # a stream ended inside the thinking
            if call_label:
                relabel(call_label)  # or inside a call, its tool never run
            streamer.flush()
            # Cancel-and-keep: closing the generator makes the backend
            # record the partial; a plain iterator has nothing to close.
            close = getattr(events, "close", None)
            if callable(close):
                close()

    if end_row:
        _end_row(chat, out, story_mode=is_story_mode and in_text)

    interrupted = interrupted or (stop_asked and not finished)
    if interrupted:
        # The cut line is ended — the prose, a block or the thinking
        # stopped mid-line. A wait cut before anything showed has no line
        # to end: the echo's blank stands as the gap, and a newline here
        # would be a second blank, with the loop's gap making a third.
        if streamed:
            # a Ctrl+R's erase counts the row ended (`erase_reply_tail`)
            open_row = in_text and not watcher.regen_requested
            _end_row(chat, out, story_mode=is_story_mode and open_row)
        if chat.api.settings.get().verbose:
            elapsed = time.monotonic() - start
            rate = chars / elapsed if elapsed > 0 else 0.0
            stats = (
                f"[ total {elapsed:.1f}s, eval {chars} chars @ {rate:.0f} chars/s, interrupted ]"
            )
            if is_story_mode:
                chat.status_note = _unbracketed(stats)
            else:
                out.write(f"{DIM}{stats}{RESET}\n")

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
            if is_story_mode:
                # the fresh take is a paragraph under the marker, placed
                # here so the next stream does not place it again
                chat.ledger.echo_flow(inline=False)
                chat.ledger.reply_begins()
        return True
    if chat.api.settings.get().notification and not interrupted:
        # The turn is over and the screen wants its reader back. Not
        # after a Ctrl+C: whoever pressed it is already here.
        ring(chat.api.settings.terminal.get().notification_sound)
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
        if sys.platform == "win32" or termios is None or tty is None:
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
        if sys.platform == "win32":
            return
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


def _unbracketed(stats: str) -> str:
    """The stats line without the `[ … ]` it is framed in under a reply:
    on the status row, a row of its own, the frame is not needed."""
    if stats.startswith("[ ") and stats.endswith(" ]"):
        return stats[2:-2]
    return stats


def _end_row(chat: Chat, out: TextIO, *, story_mode: bool) -> None:
    """End the row the stream stopped on — but where it stopped mid-row
    in story mode's text (`story_mode`: the text drawn last, no block),
    leave the row open: the next prompt stands there, back in the text,
    and a newline would scroll the text up a row at the screen's foot,
    and show the cursor dropping under it and back (`ledger.hold_at_tail`)."""
    if story_mode and chat.ledger.mid_row:
        chat.ledger.hold_at_tail()
    else:
        out.write("\n")


def question_to_drop(chat: Chat) -> list[Message] | None:
    """The story as it stands, where a line about to be played answers —
    or a continue goes past — the question its newest turn asked, in
    story mode; None otherwise. Read BEFORE the line is sent: the reply
    starts the moment the line is accepted, and records the line first."""
    if chat.layout.mode is not StoryMode.STORY or not chat.posed:
        return None
    return chat.messages()


def drop_question(chat: Chat, rows: list[Message] | None) -> None:
    """In story mode the question a line is about to answer — or a
    continue to go past — is past, and a past question is no part of the
    one text (`render.flow`): the reply that asked it, with its tool
    chain, is taken off the screen (/regen's erase) and drawn again
    without it, ended as a
    finished reply ends — its newline, the blank under it — so what
    comes next goes on from the reply's prose. `rows` is the story as
    `question_to_drop` read it, before the line; None draws nothing. Called
    before the stream is shown: inside it the key watcher owns the
    terminal's answers, which the erase asks for. Where the erase cannot
    be proven, the question stays."""
    if rows is None:
        return
    # the question alone, where the stream noted its start: the prose above
    # it stands as it is, the answer going on from its end
    if chat.ledger.take_question():
        return
    shown_turns = chat.ledger.reply_turns
    if not chat.ledger.erase_reply():
        # the question stands; the answer goes under it, not on after it
        chat.ledger.invalidate()
        return
    # the reply with its tool chain — as much of it as the screen showed:
    # the erase took that
    items = list(rows)
    first = (
        reply_start_index(items, len(items)) if shown_turns is None else len(items) - shown_turns
    )
    # a cut reply opened the echo: nothing on screen before it to join
    before = items[first - 1] if first and shown_turns is None else None
    piece = flow(items[first:])
    if before is None:
        lead = ""
    elif items[first].join_type is JoinType.INLINE:
        lead = " "
    else:
        lead, piece = "\n\n", piece.lstrip("\n")
    chat.ledger.reply.write(lead + piece + "\n")
    chat.ledger.reply_turns = shown_turns  # the same part drawn again
    # the blank a finished reply stands over, as the loop's gap prints it
    print()
    chat.ledger.note_printed("\n")
