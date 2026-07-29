"""The background lore worker: idle-debounced extraction passes.

One daemon thread owns its own store connection (WAL makes the concurrent
write safe) and runs `extraction.run` while the user is idle at the
prompt — never on the exit path, so extracting never blocks the user and a
goodbye never triggers a cold model load. It is the system log's owner and
only writer: everything a pass does lands there.

Control from the REPL thread:

- `schedule(job)` after a model turn — arm/re-arm the idle timer with the
  latest snapshot. Latest-wins over the QUEUE; it never preempts a run.
  The manual close uses the same door with `now=True`, so there is exactly
  one path into a pass and two can never overlap.
- `touch()` on every keystroke — push a PENDING job's deadline back to a
  full idle window, so a pass starts on real idle, not mid-composition.
- `defer()` on any submission — the user is active: drop the pending job
  and skip the warm-up, but let a running pass finish. Killing it would
  starve extraction for a player whose think-time is shorter than a pass —
  and silently, since a cancelled pass writes nothing at all. Letting it
  finish is harmless: it covers settled messages that already happened.
- `shutdown()` on exit — abort everything, including mid-stream, and
  return immediately (the thread is a daemon; a half-finished scene never
  commits).

While a job runs, the pass reports its current step via `status()` /
`on_status` (the REPL's activity toolbar). A small display thread holds
the LAST line of a pass on screen ≥ `min_dwell` seconds before the clear,
so a step a fast server finishes in milliseconds is still readable —
display-only: the worker never blocks on it. A failed pass's reason stays
on the row until the next pass starts.

After a scene closes, the warm-up: the close rewrites the next request's
shape, so the server's cached prefix is stale. The exact prompt the
session will send next (rebuilt from the job's snapshot) is sent with
max_tokens=1 while the user is still reading — skipped once they have
moved on, because the prefix it would warm is one they are already past.
"""

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from otaku.logs.system import SystemLog
from otaku.lore import assembler
from otaku.lore.extraction import Extractor, PassResult, Report
from otaku.providers.base import OpenAIClient
from otaku.providers.registry import Registry as ProviderRegistry
from otaku.settings.config import Config
from otaku.settings.prompts import Prompts
from otaku.store import Store
from otaku.store.schema import Message

# Each final status line stays on screen at least this long, so a pass a
# fast server blows through is still readable. Cosmetic only — enforced on
# the display side, never by holding up the worker's model calls.
_MIN_STATUS_DWELL = 5.0


@dataclass(frozen=True)
class Job:
    """One scheduled pass: the story to extract from, plus everything the
    warm-up needs to rebuild the request the user's next turn will send —
    the snapshot carries the session's shaping settings, not just the
    transcript. `on_done` fires once the pass returns — whatever the
    outcome, and before the warm-up — so the manual close can wait on it
    and report the result without the worker ever printing."""

    provider_name: str
    model: str
    story_id: int
    prompts: Prompts
    config: Config  # frozen — as snapshot-safe as copying its fields out
    system: str = ""
    messages: list[Message] = field(default_factory=list)
    force: bool = False  # the manual close: gate and settle dropped
    on_done: Callable[[PassResult, Report], None] | None = None

    # `assembler.StoryView` — the warm-up assembles from the job.

    @property
    def recap_header(self) -> str:
        return self.prompts.recap_header

    @property
    def head_messages(self) -> int:
        return self.config.head_messages

    @property
    def tail_messages(self) -> int:
        return self.config.tail_messages


class LoreWorker:
    def __init__(
        self,
        store_factory: Callable[[], Store],
        providers: ProviderRegistry,
        system_log: SystemLog,
        *,
        idle_seconds: float,
        min_dwell: float = _MIN_STATUS_DWELL,
    ) -> None:
        self._store_factory = store_factory
        self._providers = providers
        self._log = system_log
        self._idle = idle_seconds
        self._min_dwell = min_dwell
        # `_desired` is what the worker last asked to show; `_shown` is what
        # the display thread has actually put on screen.
        self._desired = ""
        self._shown = ""
        self._shown_at: float | None = None
        self._last_line = ""  # newest non-empty request, for a held failure
        self.on_status: Callable[[], None] | None = None
        self._cond = threading.Condition()
        self._pending: Job | None = None
        self._deadline: float | None = None
        # Two levels of stop. `_abort` is hard (shutdown): it kills the pass
        # mid-stream. `_deferred` is soft (the user typed): it only skips
        # the work that is pointless once they have moved on.
        self._abort: threading.Event | None = None
        self._deferred: threading.Event | None = None
        self._stopped = False
        self._thread: threading.Thread | None = None
        self._display: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="otaku-lore", daemon=True)
        self._display = threading.Thread(
            target=self._display_loop, name="otaku-status", daemon=True
        )
        self._thread.start()
        self._display.start()

    def schedule(self, job: Job, *, now: bool = False) -> None:
        """Queue a job, to run after the idle debounce — or immediately with
        `now` (the manual close: you asked, there is nothing to wait for)."""
        with self._cond:
            if self._stopped:
                return
            self._pending = job
            self._deadline = time.monotonic() + (0 if now else self._idle)
            self._cond.notify_all()

    def touch(self) -> None:
        """The user is typing: a PENDING job's deadline moves to a full
        idle window from now. A running job is left alone (same rule as
        `defer`); with nothing pending this is a no-op. Called on every
        buffer change, so it must stay this cheap."""
        with self._cond:
            if self._pending is not None and self._deadline is not None:
                self._deadline = time.monotonic() + self._idle

    def cancel(self) -> None:
        """Abort the RUNNING pass mid-stream and drop anything queued — the
        user said stop (Ctrl+C on a foreground wait). Unlike `defer`, this
        kills in-flight work; nothing half-done commits, and scenes the
        pass already closed stay."""
        with self._cond:
            self._pending = None
            self._deadline = None
            if self._abort is not None:
                self._abort.set()
            self._cond.notify_all()

    def defer(self) -> None:
        """The user submitted: drop the queued job and skip the warm-up,
        but leave a running pass alone. Non-blocking, and deliberately
        unlogged — it fires on every submission, and the system log records
        work, not scheduling."""
        with self._cond:
            self._pending = None
            self._deadline = None
            if self._deferred is not None:
                self._deferred.set()
            self._cond.notify_all()

    def shutdown(self) -> None:
        """Stop the worker. Non-blocking — never joins the daemon thread,
        so exit is immediate; an unfinished pass simply never commits."""
        with self._cond:
            self._stopped = True
            self._pending = None
            self._deadline = None
            for event in (self._abort, self._deferred):
                if event is not None:
                    event.set()
            self._cond.notify_all()

    def get_status(self) -> str:
        """What is on screen right now — "" when idle. Read from the REPL
        thread; a str rebind is atomic, so no lock is needed."""
        return self._shown

    # ---------- worker internals ----------

    def _loop(self) -> None:
        store: Store | None = None
        try:
            while True:
                job = self._next_job()
                if job is None:
                    if self._stopped:
                        return
                    continue  # deferred during the debounce; wait for the next
                # Background best-effort: a failure (DB open, HTTP) must never
                # surface a traceback under the live prompt.
                held = ""
                # What on_done reports when the pass dies before returning —
                # a waiting manual close must never hang on a crash.
                result = PassResult.FAILED
                report = Report()
                try:
                    with contextlib.suppress(Exception):
                        self._set_status("")  # a new pass supersedes a held failure
                        if store is None:
                            store = self._store_factory()
                        client = self._providers.get_client(job.provider_name)
                        extractor = Extractor(
                            store,
                            client,
                            job.model,
                            job.story_id,
                            job.prompts,
                            cancel=self._abort,
                            progress=self._set_status,
                            log=self._log.record,
                        )
                        result, report = extractor.run(
                            settle=job.config.settle_messages,
                            min_chars=job.config.scene_min_chars,
                            min_messages=job.config.scene_min_messages,
                            force=job.force,
                        )
                        if result is PassResult.FAILED:
                            # The status row is always reserved, so a failure
                            # can sit in it until the next pass — otherwise
                            # the clear below overwrites the notice before
                            # anyone sees it.
                            held = self._last_line
                    # Before the warm-up: the waiter asked for the scene
                    # close, not for a prefill it was never going to watch.
                    if job.on_done is not None:
                        with contextlib.suppress(Exception):
                            job.on_done(result, report)
                    with contextlib.suppress(Exception):
                        if result is PassResult.CLOSED and store is not None:
                            self._warm(store, client, job)
                finally:
                    # Idle — unless the pass failed, in which case its
                    # reason stays on screen until the next one starts.
                    self._set_status(held)
        finally:
            if store is not None:
                store.close()

    def _next_job(self) -> Job | None:
        """Block until a scheduled job's idle debounce elapses, then claim
        it. None when the worker is stopping or the job was deferred."""
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
            self._abort = threading.Event()
            self._deferred = threading.Event()
            return job

    def _warm(self, store: Store, client: OpenAIClient, job: Job) -> None:
        """Prefill the server's cache with the request the next turn will
        send — the arguments mirror the session's `assemble` call because
        the prompt must match byte for byte; a warm-up of a slightly
        different prefix caches nothing useful."""
        assert self._deferred is not None
        if not job.messages or self._deferred.is_set():
            return
        self._set_status("warming the prompt cache")
        try:
            context = client.get_context_size(job.model)
        except Exception:
            context = None
        wire = assembler.assemble_story(store, job, context).messages
        # One token: the point is the prefill, not the answer. The cancel
        # here is the SOFT flag: the user typing makes the prefill stale,
        # not just the shutdown.
        Extractor(
            store,
            client,
            job.model,
            job.story_id,
            job.prompts,
            cancel=self._deferred,
            log=self._log.record,
        ).complete(wire, "warm", params={"temperature": 0, "max_tokens": 1})
        if not self._deferred.is_set():
            self._log.record(f"prompt cache warmed (story {job.story_id})")

    # ---------- the status row ----------

    def _set_status(self, text: str) -> None:
        """Request a status line. Returns at once — the display thread
        decides when it appears, so the worker never waits on the dwell."""
        with self._cond:
            if text == self._desired:
                return
            self._desired = text
            if text:
                self._last_line = text
            self._cond.notify_all()

    def _display_loop(self) -> None:
        """Move `_shown` toward `_desired`. The row's ONE job is to say what
        the pass is doing RIGHT NOW, so a new step shows at once: a finished
        step must never outrank a running one. The dwell applies only to
        the CLEAR — the last line of a pass is held ≥ `min_dwell` so a step
        a fast server finishes in milliseconds is still readable before the
        row goes blank."""
        while True:
            with self._cond:
                while not self._stopped and self._desired == self._shown:
                    self._cond.wait()
                if self._stopped:
                    return
                if not self._desired and self._shown and self._shown_at is not None:
                    remaining = self._min_dwell - (time.monotonic() - self._shown_at)
                    if remaining > 0:
                        self._cond.wait(timeout=remaining)
                        continue
                self._shown = self._desired
                self._shown_at = time.monotonic()
                callback = self.on_status
            if callback is not None:
                # A repaint failure must never take down the display thread.
                with contextlib.suppress(Exception):
                    callback()
