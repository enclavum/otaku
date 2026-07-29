"""The model-facing inference loop: streaming and the in-stream Ctrl+R
watcher. Session state lives in `chat/state.py`; the slash-command handlers
build on both.
"""

import contextlib
import os
import select
import signal
import sys
import threading
import time
from typing import Any, Self

import httpx

from otaku.chat.markdown import MarkdownStreamer
from otaku.chat.state import Session
from otaku.formatting import format_context
from otaku.lore import assembler
from otaku.providers.base import Stats, Text, Thinking
from otaku.settings.config import Provider
from otaku.store import Store
from otaku.store.schema import Message
from otaku.term.ansi import DIM, RESET
from otaku.term.spinner import Spinner

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
        except ValueError, OSError:
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
            except OSError, ValueError:
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


def run_inference(session: Session, store: Store, *, ooc: bool = False) -> None:
    """Stream a completion for the current transcript, append the reply, and
    persist it. A cancelled stream always keeps the received portion: Ctrl+C
    stops and leaves it as the reply; Ctrl+R stops and immediately
    regenerates (looping here until no further regen is requested), the
    partial surviving in the tree as a sibling like any regenerated reply.
    `ooc` marks the REPLY out of character (kind `ooc`) — set only by /ooc
    and a regenerate of an ooc reply, never inferred, because a /you switch
    also ends on an ((OOC:)) turn yet wants an in-character answer."""
    _run_step(session, store, ooc)
    while session.regen_after:
        session.regen_after = False
        session.drop_last_reply(store)
        _run_step(session, store, ooc)


def _run_step(session: Session, store: Store, ooc: bool) -> None:
    """One streaming pass. ^C during the stream is consumed here: partial
    output is kept and persisted so the user can regenerate or continue.
    ^R persists the partial the same way, then sets `session.regen_after`
    for the outer loop, whose drop_last_reply leaves it as a sibling."""
    content: list[str] = []
    in_thinking = False
    final: Stats | None = None
    interrupted = False
    error: str | None = None

    spinner = Spinner()
    spinner.start()
    start = time.monotonic()
    watcher = _StreamWatcher()
    renderer = MarkdownStreamer()
    client = session.providers.get_client(session.provider.name)
    wire = assembler.assemble_story(store, session, client.get_context_size(session.model)).messages
    # The activity line survives the prompt's absence: entering `status_row`
    # reserves the bottom terminal row and paints the worker's status there
    # for the whole stream; leaving it releases the row.
    status_row = session.status_line.pinned() if session.status_line else contextlib.nullcontext()
    with status_row, watcher:
        try:
            for chunk in client.chat_stream(
                session.model, wire, session.params, think=session.think, purpose="chat"
            ):
                spinner.stop()  # idempotent — the first real signal clears it
                if isinstance(chunk, Thinking):
                    if not in_thinking:
                        sys.stdout.write(DIM + "(thinking) ")
                        in_thinking = True
                    sys.stdout.write(chunk.text)
                    sys.stdout.flush()
                elif isinstance(chunk, Text):
                    if in_thinking:
                        sys.stdout.write(RESET + "\n")
                        in_thinking = False
                    renderer.feed(chunk.text)
                    content.append(chunk.text)
                elif isinstance(chunk, Stats):
                    final = chunk
        except KeyboardInterrupt:
            interrupted = True
        except Exception as e:
            error = _error_message(e, session.provider)
        finally:
            spinner.stop()
            renderer.flush()

    if in_thinking:
        sys.stdout.write(RESET)
    if error is not None:
        print(f"\n[error: {error}]")
        return
    sys.stdout.write("\n")

    elapsed = time.monotonic() - start
    if session.verbose and interrupted:
        chars = sum(len(c) for c in content)
        rate = chars / elapsed if elapsed > 0 else 0.0
        print(
            f"{DIM}[ total {elapsed:.1f}s, "
            f"eval {chars} chars @ {rate:.0f} chars/s, interrupted ]{RESET}"
        )
    elif session.verbose and final is not None:
        print(DIM + format_stats(final) + RESET)

    if content:
        # No speaker set here: lore extraction attributes the reply later
        # (never for the wire). `kind` still marks an ooc reply.
        session.record_turn(
            store,
            Message(
                role="assistant",
                body="".join(content),
                kind="ooc" if ooc else "dialogue",
                provider=session.provider.name,
                model=session.model,
            ),
        )
    if final is not None:
        store.usage.record(
            session.provider.name,
            session.model,
            "chat",
            story_id=session.story_id,
            prompt_tokens=final.prompt_tokens,
            completion_tokens=final.completion_tokens,
            duration_seconds=final.duration_seconds,
        )

    if watcher.regen_requested:
        # Ctrl+R during the stream: the partial is recorded above like any
        # reply; the outer loop's drop_last_reply siblings it away.
        print(f"{DIM}[ regenerating ]{RESET}")
        session.regen_after = True


def format_stats(stats: Stats) -> str:
    """The verbose stats line:

        [ total 1.3s, prompt 40 tok, eval 37 tok @ 35.2 tok/s, ctx 12% / 32K ]

    `total` is wall-clock for the whole request; the rate is computed over
    the decode-only span (excluding prefill and time-to-first-token) so it
    reflects generation speed. Fields with no underlying value are skipped."""
    parts: list[str] = [f"total {stats.duration_seconds:.1f}s"]
    if stats.prompt_tokens is not None:
        parts.append(f"prompt {stats.prompt_tokens} tok")
    if stats.completion_tokens is not None:
        generation = stats.generation_seconds or stats.duration_seconds
        if generation > 0:
            rate = stats.completion_tokens / generation
            parts.append(f"eval {stats.completion_tokens} tok @ {rate:.1f} tok/s")
        else:
            parts.append(f"eval {stats.completion_tokens} tok")
    if stats.context_max:
        cap = format_context(stats.context_max)
        if stats.prompt_tokens is not None and stats.context_max > 0:
            pct = stats.prompt_tokens / stats.context_max * 100
            parts.append(f"ctx {pct:.0f}% / {cap}")
        else:
            parts.append(f"ctx {cap}")
    return "[ " + ", ".join(parts) + " ]"


def _error_message(e: Exception, provider: Provider) -> str:
    """One formatter for every stream failure. A 4xx/5xx carries the
    server's explanatory body — a bare '400 Bad Request' hides the actual
    reason (usually context overflow)."""
    if isinstance(e, httpx.HTTPStatusError):
        body = ""
        with contextlib.suppress(Exception):
            e.response.read()  # a streamed response may not be read yet
            body = " ".join(e.response.text.split())
        detail = f": {body[:300]}" if body else ""
        return f"HTTP {e.response.status_code} from {e.request.url.host}{detail}"
    if isinstance(e, httpx.RequestError):
        return f"could not reach {provider.name} at {provider.url}"
    return str(e)
