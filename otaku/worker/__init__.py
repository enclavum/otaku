"""The background actor: idle-debounced extraction passes on its own
thread, over its own store connection (WAL makes the concurrent write
safe). Scheduled by backend — a played turn arms the idle timer, typing
pushes it back, a submission defers, a manual close forces — and driven
by the deadline, not the user; never on the exit path, so extracting
never blocks the user and a goodbye never triggers a cold model load.
One path into a pass, so a forced close can never race an automatic
one. The worker is the system log's owner and only writer: everything a
pass does lands there.

After a scene closes, the warm-up — LOCAL providers only: the close
rewrites the next request's shape, so the server's cached prefix is
stale. The exact next request (rebuilt from the Job's snapshot) is sent
with max_tokens=1 while the user still reads — skipped once they moved
on, because the prefix it would warm is one they are already past, and
never sent to a cloud provider, which has no per-session cache to warm
and would bill the full window for the one token.
"""

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from otaku.context import assembler
from otaku.context.assembler import ContextShape
from otaku.formatting import format_duration
from otaku.logging import ErrorLog, SystemLog
from otaku.providers import Locality, OpenAIClient, ProviderError, Registry
from otaku.store import Store
from otaku.store.schema import Message
from otaku.worker.extraction import ExtractionSettings, Extractor, PassResult, Report

# Each final status line stays on screen at least this long, so a pass a
# fast server blows through is still readable. Cosmetic only — enforced on
# the display side, never by holding up the worker's model calls.
_MIN_STATUS_DWELL = 5.0


@dataclass(frozen=True)
class Job:
    """One scheduled pass: the story to extract from, its extraction
    settings, and everything the warm-up needs to rebuild the next
    request — template strings and a ContextShape ride the snapshot, so
    the worker reads no settings files."""

    provider: str
    model: str
    story_id: int
    system: str  # the story's system prompt — the warm-up's assemble needs it
    messages: list[Message]
    shape: ContextShape
    extraction_settings: ExtractionSettings
    force: bool = False  # the manual close: gate and settle margin dropped
    # Fires once the pass returns — whatever the outcome, before the
    # warm-up — so a foreground wait can report without the worker printing.
    on_done: Callable[[PassResult, Report], None] | None = field(default=None, compare=False)


class Worker:
    """One daemon thread and its display twin. Control from the backend:
    `schedule` (latest wins over the queue; never preempts a run),
    `touch` (typing pushes a pending deadline back), `defer` (a
    submission drops the queue and skips the warm-up; a running pass
    finishes), `cancel` (abort mid-stream; nothing half-done commits),
    `shutdown` (non-blocking; exit is immediate). `status` is the
    one-line account the frontends show; `on_status` its repaint hook.

    While a job runs, the pass reports its current step through the
    status row. A small display thread holds the LAST line of a pass on
    screen ≥ `min_dwell` seconds before the clear, so a step a fast
    server finishes in milliseconds is still readable — display-only:
    the worker never blocks on it. A failed pass's reason stays on the
    row until the next pass starts."""

    def __init__(
        self,
        store_factory: Callable[[], Store],
        providers_registry: Registry,
        system_log: SystemLog,
        *,
        errors: ErrorLog | None = None,
        idle_seconds: float,
        min_dwell: float = _MIN_STATUS_DWELL,
    ) -> None:
        self._store_factory = store_factory
        self._providers = providers_registry
        self._log = system_log
        self._errors = errors
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
        """Start the two background threads — once. A second call is the
        same actor asking again (a frontend attaching to a session that
        already has one), and starting a second pair would put two
        workers on one story."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="otaku-worker", daemon=True)
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

    def defer(self) -> None:
        """The user submitted: drop the queued job and skip the warm-up,
        but leave a running pass alone. Killing it would starve
        extraction for a player whose think-time is shorter than a pass —
        and silently, since a cancelled pass writes nothing at all;
        letting it finish is harmless: it covers settled messages that
        already happened. Non-blocking, and deliberately unlogged — it
        fires on every submission, and the system log records work, not
        scheduling."""
        with self._cond:
            self._pending = None
            self._deadline = None
            if self._deferred is not None:
                self._deferred.set()
            self._cond.notify_all()

    def cancel(self) -> None:
        """Abort the RUNNING pass mid-stream and drop anything queued — the
        user said stop (a foreground wait interrupted). Unlike `defer`,
        this kills in-flight work; nothing half-done commits, and scenes
        the pass already closed stay."""
        with self._cond:
            self._pending = None
            self._deadline = None
            if self._abort is not None:
                self._abort.set()
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

    def status(self) -> str:
        """What the status row shows right now — "" when idle. Readable
        from any thread; a str rebind is atomic, so no lock is needed."""
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
                    try:
                        self._set_status("")  # a new pass supersedes a held failure
                        if store is None:
                            store = self._store_factory()
                        client = self._providers.get(job.provider)
                        if client is None:
                            raise ProviderError(f"Unknown provider {job.provider!r}.")
                        extractor = Extractor(
                            store,
                            client,
                            job.model,
                            job.story_id,
                            extraction_settings=job.extraction_settings,
                            cancel=self._abort,
                            progress=self._set_status,
                            log=self._log.record,
                        )
                        result, report = extractor.run(force=job.force)
                        if result is PassResult.FAILED:
                            # The status row is always reserved, so a failure
                            # can sit in it until the next pass — otherwise
                            # the clear below overwrites the notice before
                            # anyone sees it.
                            held = self._last_line
                    except Exception as e:
                        # Never a traceback under the live prompt — but
                        # never silence either: the system log records the
                        # shape of the crash (the type only; that log stays
                        # content-free), the error log the whole traceback.
                        held = f"extraction failed ({type(e).__name__})"
                        with contextlib.suppress(Exception):
                            self._log.record(
                                f"extraction crashed (story {job.story_id}): {type(e).__name__}"
                            )
                            if self._errors is not None:
                                self._errors.record(f"lore pass (story {job.story_id})", e)
                    # Before the warm-up: the waiter asked for the scene
                    # close, not for a prefill it was never going to watch.
                    if job.on_done is not None:
                        with contextlib.suppress(Exception):
                            job.on_done(result, report)
                    with contextlib.suppress(Exception):
                        if result is PassResult.CLOSED and store is not None and client is not None:
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
        send — the arguments mirror the session's `assemble_story` call
        because the prompt must match byte for byte; a warm-up of a
        slightly different prefix caches nothing useful.

        LOCAL providers only: the warm-up exists for a local server's
        prefix cache, so the close's rewrite of the window costs no
        first-token wait. A hosted catalog keeps no per-session cache an
        OpenAI-compatible request could warm — the same request there is
        a full context window BILLED for one token, so it is never
        sent; nor to the generic provider, whose url could name one."""
        assert self._deferred is not None
        if client.locality is not Locality.LOCAL:
            return
        if not job.messages or self._deferred.is_set():
            return
        started = time.monotonic()
        self._log.record(f"warm-up started (story {job.story_id})")
        self._set_status("warming the prompt cache")
        try:
            found = client.models.get(job.model)
            max_context = found.max_context if found else None
        except Exception:
            max_context = None
        try:
            wire = assembler.assemble_story(
                store,
                job.story_id,
                system=job.system,
                messages=job.messages,
                shape=job.shape,
                max_context=max_context,
            ).messages
        except assembler.ContextOverflowError:
            # Nothing sendable to warm with — the next turn will say so.
            self._log.record(f"warm-up skipped (story {job.story_id}): context over the limit")
            return
        # One token: the point is the prefill, not the answer. The cancel
        # here is the SOFT flag: the user typing makes the prefill stale,
        # not just the shutdown.
        Extractor(
            store,
            client,
            job.model,
            job.story_id,
            extraction_settings=job.extraction_settings,
            cancel=self._deferred,
            log=self._log.record,
        ).complete(wire, "warm", params={"temperature": 0, "max_tokens": 1})
        if not self._deferred.is_set():
            self._log.record(
                f"warm-up finished (story {job.story_id}) "
                f"({format_duration(time.monotonic() - started)})"
            )

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
