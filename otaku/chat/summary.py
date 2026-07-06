"""LLM-generated conversation summaries.

Summaries are short, searchable, language-matching sentences shown in the
`/history` picker. They are produced by a background `SummaryWorker` that runs
while the user is idle at the prompt — never on the exit path — so summarizing
never blocks the user and a goodbye never triggers a cold model reload.

Cadence: after each assistant turn the REPL `schedule()`s the current
conversation; the worker waits out an idle debounce (config
`summary_idle_seconds`) and then summarizes with its own DB connection. Any new
submission `cancel()`s a pending or in-flight summary so the user's next prompt
never queues behind it. `shutdown()` (on exit) cancels and returns immediately —
the worker is a daemon thread, so a half-finished summary just never commits
(WAL keeps the DB consistent) and stays flagged for the conversation's next open.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from uuid import UUID

from otaku.client import ContentDelta, client_for
from otaku.config import Provider
from otaku.storage.store import Message, Store

SUMMARY_PROMPT = (
    "Task: write a 1-2 sentence summary (<=30 words) of our conversation so far.\n"
    "LANGUAGE RULE: the summary MUST be written in the same language as our "
    "conversation. Spanish -> Spanish summary. German -> German. English -> "
    "English. Do NOT translate to English; match the conversation's language.\n"
    "Output the summary only, with no preamble, label, or quotes."
)

SUMMARY_MAX_TOKENS = 100
SUMMARY_TIMEOUT = 30.0

# A summarize job: (provider, model, conv_id, messages snapshot).
_Job = tuple[Provider, str, UUID, list[Message]]


def generate_summary(
    store: Store,
    provider: Provider,
    model: str,
    conv_id: UUID,
    messages: list[Message],
    cancel: threading.Event | None = None,
) -> str | None:
    """Generate and persist a summary if the conversation has changed since the
    last one. Silent (no spinner/print — safe to run under the live prompt) and
    cancellable: if `cancel` fires, the HTTP stream is torn down promptly and
    nothing is written. Returns the new summary, or None if not needed / cancelled
    / failed.
    """
    if not messages or (cancel is not None and cancel.is_set()):
        return None
    if not store.needs_summary(conv_id):
        return None

    augmented = [*messages, Message(role="user", content=SUMMARY_PROMPT)]
    buf: list[str] = []
    gen = client_for(provider).chat_stream(
        model,
        augmented,
        {"max_tokens": SUMMARY_MAX_TOKENS, "temperature": 0.2},
        think="none",
        timeout=SUMMARY_TIMEOUT,
    )
    try:
        for chunk in gen:
            if cancel is not None and cancel.is_set():
                return None  # finally closes the stream → server stops generating
            if isinstance(chunk, ContentDelta):
                buf.append(chunk.text)
    except Exception:
        return None
    finally:
        close = getattr(gen, "close", None)
        if callable(close):
            close()

    if cancel is not None and cancel.is_set():
        return None
    summary = "".join(buf).strip()
    if not summary:
        return None
    store.update_summary(conv_id, summary)
    return summary


class SummaryWorker:
    """Background, idle-debounced conversation summarizer.

    One daemon thread owns its own `Store` connection (WAL makes the concurrent
    write safe). Control from the REPL thread:

    - `schedule(...)` after an assistant turn — arm/re-arm the idle timer with
      the latest snapshot (latest-wins; supersedes any in-flight generation).
    - `cancel()` on any new submission — drop the pending timer and abort an
      in-flight generation so the user's prompt never waits behind it.
    - `shutdown()` on exit — cancel and stop; never joins, so exit is immediate.
    """

    def __init__(self, store_factory: Callable[[], Store], idle_seconds: float) -> None:
        self._store_factory = store_factory
        self._idle = idle_seconds
        self._cond = threading.Condition()
        self._pending: _Job | None = None
        self._deadline: float | None = None
        self._active_cancel: threading.Event | None = None
        self._stopped = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="otaku-summary", daemon=True)
        self._thread.start()

    def schedule(
        self, provider: Provider, model: str, conv_id: UUID, messages: list[Message]
    ) -> None:
        with self._cond:
            if self._stopped:
                return
            self._pending = (provider, model, conv_id, list(messages))
            self._deadline = time.monotonic() + self._idle
            if self._active_cancel is not None:
                self._active_cancel.set()  # latest-wins: supersede a running summary
            self._cond.notify_all()

    def cancel(self) -> None:
        """Drop any pending summary and abort an in-flight one. Non-blocking."""
        with self._cond:
            self._pending = None
            self._deadline = None
            if self._active_cancel is not None:
                self._active_cancel.set()
            self._cond.notify_all()

    def shutdown(self) -> None:
        """Stop the worker. Non-blocking — never joins the daemon thread, so the
        app exits immediately; an unfinished summary simply never commits."""
        with self._cond:
            self._stopped = True
            self._pending = None
            self._deadline = None
            if self._active_cancel is not None:
                self._active_cancel.set()
            self._cond.notify_all()

    def _next_job(self) -> _Job | None:
        """Block until a scheduled job's idle debounce elapses, then claim it.
        Returns None when the worker is stopping or the job was cancelled."""
        with self._cond:
            while not self._stopped and self._pending is None:
                self._cond.wait()
            while not self._stopped and self._pending is not None:
                assert self._deadline is not None
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=remaining)
            if self._stopped or self._pending is None:
                return None
            job = self._pending
            self._pending = None
            self._active_cancel = threading.Event()
            return job

    def _loop(self) -> None:
        store: Store | None = None
        try:
            while True:
                job = self._next_job()
                if job is None:
                    if self._stopped:
                        return
                    continue  # cancelled during the debounce; wait for the next
                # Background best-effort: a failure (DB open, HTTP) must never
                # surface a traceback under the live prompt — swallow and move on.
                with contextlib.suppress(Exception):
                    if store is None:
                        store = self._store_factory()
                    provider, model, conv_id, messages = job
                    generate_summary(store, provider, model, conv_id, messages, self._active_cancel)
        finally:
            if store is not None:
                store.close()
