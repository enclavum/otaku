"""Session state + the model-facing inference loop.

`State.messages` is the source of truth and matches what's sent to the model
(including a leading system message at index 0 when set). Every mutation that
should survive a crash is followed by `store.snapshot_messages` (via `persist`)
so the DB row mirrors the in-memory list at all times.

This module is the "talk to the model" half of the REPL — streaming, the
in-stream Ctrl+R watcher, and one-shot output — separated from the slash-command
handlers in `commands.py`, which build on the primitives defined here.
"""

from __future__ import annotations

import contextlib
import os
import select
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx

from otaku.chat.mdstream import MarkdownStreamer
from otaku.chat.stats import format_stats
from otaku.client import ContentDelta, FinalStats, ThinkingDelta, client_for
from otaku.config import Config, Provider
from otaku.spinner import Spinner
from otaku.storage.store import Message, Store

# POSIX-only raw-terminal control for the in-stream Ctrl+R watcher. Absent on
# Windows (no termios/tty) — the watcher degrades to a no-op there so the rest
# of otaku still runs; Ctrl+C cancellation goes through the kernel regardless.
try:
    import termios
    import tty
except ImportError:  # pragma: no cover - exercised only on Windows
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

DIM = "\x1b[2m"
RESET = "\x1b[0m"


@dataclass
class State:
    config: Config
    provider: Provider
    model: str  # bare model name as the server expects it
    full_model: str  # "<provider>/<model>" for storage and prompt display
    conv_id: UUID | None = None  # created lazily on first persistable turn
    messages: list[Message] = field(default_factory=list)
    params: dict[str, object] = field(default_factory=dict)
    # Thinking effort: one of "low" | "medium" | "high" | "max" | "none".
    # Defaults to "none" — thinking is off unless explicitly turned on, so
    # thinking-by-default models don't surprise the user. `None` means
    # "don't send any field" → defer to the model/backend default (reach it
    # with `/set think default`). `/set think on` aliases "medium", `off`
    # aliases "none". How the value reaches the wire is provider-specific
    # (see ProviderClient._apply_thinking).
    think: str | None = "none"
    # Show the `[ total … tok/s … ]` stats line after each reply. Off by
    # default; toggled with `/set verbose on|off`.
    verbose: bool = False
    quit: bool = False
    # Set by the in-stream Ctrl+R watcher to request an immediate
    # regenerate after the current reply is cancelled. Drained by
    # `run_inference`'s outer loop.
    regen_after: bool = False


def _has_real_turn(messages: list[Message]) -> bool:
    """True if messages contain anything beyond a leading system message."""
    return any(m.role != "system" for m in messages)


def persist(state: State, store: Store) -> None:
    """Snapshot state.messages to the DB. Lazily creates a conversation row
    on first call when there's an actual user/assistant turn — so /set system
    or /bye-ing immediately after `otaku run` doesn't leave an empty row.
    """
    if not _has_real_turn(state.messages):
        return
    if state.conv_id is None:
        state.conv_id = store.create_conversation(state.full_model)
    store.snapshot_messages(state.conv_id, state.messages)


_CTRL_R = b"\x12"


class _StreamWatcher:
    """While streaming, put the TTY in cbreak mode and watch for Ctrl+R.
    On Ctrl+R, set `regen_requested` and raise SIGINT so the main thread's
    blocking stream read interrupts. Ctrl+C still works through the kernel
    (ISIG is left on by cbreak). On exit, any pending input is discarded
    so it doesn't leak into the next prompt. No-op when stdin isn't a TTY.
    """

    def __init__(self) -> None:
        self.regen_requested = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._orig: Any | None = None
        self._fd: int = -1

    def __enter__(self) -> _StreamWatcher:
        if termios is None or tty is None:
            return self  # Windows: no raw-terminal control — watcher stays idle
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
                r, _, _ = select.select([self._fd], [], [], 0.05)
            except (OSError, ValueError):
                return
            if not r:
                continue
            try:
                b = os.read(self._fd, 1)
            except OSError:
                return
            if not b:
                return
            if b == _CTRL_R:
                self.regen_requested = True
                os.kill(os.getpid(), signal.SIGINT)
                return


def run_inference(state: State, store: Store) -> None:
    """Stream a chat completion against current state.messages, append the
    assistant reply, and snapshot to DB. Ctrl+C cancels the stream and
    keeps the partial reply. Ctrl+R cancels the stream and immediately
    regenerates (loops here until no further regen is requested)."""
    _run_inference_step(state, store)
    while state.regen_after:
        state.regen_after = False
        if state.messages and state.messages[-1].role == "assistant":
            state.messages.pop()
            persist(state, store)
        _run_inference_step(state, store)


def _run_inference_step(state: State, store: Store) -> None:
    """One streaming pass. ^C during the stream is consumed here: prints
    partial stats and saves whatever was received so the user can
    /regenerate or continue. ^R sets state.regen_after for the outer
    loop to consume and discards the partial reply."""
    content_buf: list[str] = []
    in_thinking = False
    final: FinalStats | None = None
    interrupted = False

    spinner = Spinner()
    spinner.start()
    start = time.monotonic()
    watcher = _StreamWatcher()
    md = MarkdownStreamer()
    with watcher:
        try:
            for chunk in client_for(state.provider).chat_stream(
                state.model,
                state.messages,
                state.params,
                think=state.think,
            ):
                spinner.stop()  # idempotent — first real signal clears the spinner
                if isinstance(chunk, ThinkingDelta):
                    if not in_thinking:
                        sys.stdout.write(DIM + "(thinking) ")
                        in_thinking = True
                    sys.stdout.write(chunk.text)
                    sys.stdout.flush()
                elif isinstance(chunk, ContentDelta):
                    if in_thinking:
                        sys.stdout.write(RESET + "\n")
                        in_thinking = False
                    md.feed(chunk.text)
                    content_buf.append(chunk.text)
                elif isinstance(chunk, FinalStats):
                    final = chunk
        except KeyboardInterrupt:
            interrupted = True
        except httpx.RequestError:
            spinner.stop()
            md.flush()
            if in_thinking:
                sys.stdout.write(RESET)
            print(f"\n[error: could not reach {state.provider.name} at {state.provider.url}]")
            return
        except Exception as e:
            spinner.stop()
            md.flush()
            if in_thinking:
                sys.stdout.write(RESET)
            print(f"\n[error: {e}]")
            return
        finally:
            spinner.stop()
            md.flush()

    if in_thinking:
        sys.stdout.write(RESET)
    sys.stdout.write("\n")

    if watcher.regen_requested:
        # Ctrl+R during stream: discard partial, queue regenerate.
        print(f"{DIM}[ regenerating ]{RESET}")
        state.regen_after = True
        return

    elapsed = time.monotonic() - start
    if state.verbose and interrupted:
        chars = sum(len(c) for c in content_buf)
        rate = chars / elapsed if elapsed > 0 else 0.0
        print(
            f"{DIM}[ total {elapsed:.1f}s, "
            f"eval {chars} chars @ {rate:.0f} chars/s, interrupted ]{RESET}"
        )
    elif state.verbose and final is not None:
        print(DIM + format_stats(final) + RESET)

    if content_buf:
        state.messages.append(Message(role="assistant", content="".join(content_buf)))
        persist(state, store)


def run_oneshot(state: State, store: Store, prompt: str) -> None:
    """One-shot, pipe-friendly completion: send `prompt` as a single user turn,
    stream the reply to stdout as plain text — no spinner, markdown styling, or
    stats line — then persist the exchange. Errors go to stderr so stdout stays
    clean for shell pipelines. Ctrl+C keeps the partial reply."""
    state.messages.append(Message(role="user", content=prompt))
    persist(state, store)

    buf: list[str] = []
    try:
        for chunk in client_for(state.provider).chat_stream(
            state.model, state.messages, state.params, think=state.think
        ):
            if isinstance(chunk, ContentDelta):
                sys.stdout.write(chunk.text)
                sys.stdout.flush()
                buf.append(chunk.text)
    except KeyboardInterrupt:
        pass
    except httpx.RequestError:
        print(
            f"[error: could not reach {state.provider.name} at {state.provider.url}]",
            file=sys.stderr,
        )
        return
    except Exception as e:
        print(f"[error: {e}]", file=sys.stderr)
        return

    if buf and not buf[-1].endswith("\n"):
        sys.stdout.write("\n")  # newline-terminate so shell prompts start clean
        sys.stdout.flush()
    if buf:
        state.messages.append(Message(role="assistant", content="".join(buf)))
        persist(state, store)
