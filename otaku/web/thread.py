"""Work handed to the thread that owns the app.

A sqlite connection answers only the thread that created it — the store
is built that way, and the background worker keeps its own connection for
the same reason — while an HTTP server answers on a thread per
connection. So a handler never touches the app: it hands work to the
thread that owns it and waits for the answer. Serialization comes free,
because there is only ever one thread in there.

The cost of one thread is that a long job holds it, and the longest job
in otaku is a reply arriving token by token. So reads are queued apart
and answered in the gaps between frames: browsing the stories, opening
the lore, previewing the context all work while the model is talking,
without a second connection and without two callers in the app at
once.

This is the one piece of the package a reader must understand
independently of HTTP, which is why it is not in the HTTP file.
"""

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

__all__ = ["AppRunner", "StoppingError"]


class StoppingError(Exception):
    """The app's thread is gone: otaku is on its way out, and the
    job will not be run. Not a crash — the one answer a request can get
    that nothing should be recorded for."""


# How long the app thread sleeps before looking up when nothing has
# arrived — short enough that Ctrl+C is felt, and never the latency of a
# request, which wakes the wait itself.
_QUEUE_TICK = 0.25

# The app the work runs on — any object: the runner never looks inside it.
A = TypeVar("A")
T = TypeVar("T")


@dataclass
class _Work(Generic[A]):
    """One call waiting for the app's thread, and the answer coming
    back — a value or the exception it raised, so a failure surfaces at
    the caller rather than dying quietly in another thread."""

    call: Callable[[A], Any]
    done: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None

    def execute(self, app: A) -> None:
        try:
            self.value = self.call(app)
        except BaseException as e:  # carried to the caller, never swallowed
            self.error = e
        finally:
            self.done.set()


class AppRunner(Generic[A]):
    """The queue between the request threads and the app's own.

    TWO queues, and the difference between them is what a job may do. A
    write moves the story, so it waits its turn behind whatever is
    already running — a reply, most of the time, which is minutes of
    model. A READ moves nothing: it can be answered in the gaps of a
    reply that is streaming, and it has to be, or every screen in the
    app is dead until the model stops talking.
    """

    def __init__(self, app: A) -> None:
        self._app = app
        self._writes: queue.Queue[_Work[A]] = queue.Queue()
        self._reads: queue.Queue[_Work[A]] = queue.Queue()
        # Set by every put, so an idle loop wakes on the work rather than
        # on the clock. Without it the tick below is not just how long a
        # stop takes to notice — it is how long every screen takes to
        # open, on a machine doing nothing at all.
        self._arrived = threading.Event()
        self._closed = False

    def run(self, work: Callable[[A], T], *, reading: bool = False) -> T:
        """Run `work` on the app's thread; what it raises is raised
        again HERE, where the request that caused it can answer for it.
        `reading` promises that `work` only READS the app — it is
        what lets the job run between the frames of a reply."""
        if self._closed:
            raise StoppingError
        job: _Work[A] = _Work(work)
        (self._reads if reading else self._writes).put(job)
        self._arrived.set()
        if self._closed:
            # `abandon` may have swept the queues between the check above
            # and the put: nothing will ever execute this job, so it is
            # answered here rather than waited on forever. Both answering
            # is harmless — same error, and `done` sets twice.
            job.error = StoppingError()
            job.done.set()
        job.done.wait()
        if job.error is not None:
            raise job.error
        return job.value  # type: ignore[no-any-return]

    def loop(self, until: threading.Event) -> None:
        """Serve both queues on the caller's thread until `until` is set,
        finishing the job in hand first.

        The flag is CLEARED before the queues are looked at, never after:
        a job arriving between the look and the wait sets it again, and
        the wait returns at once instead of sleeping on work that is
        already there. The timeout is what makes `until` noticed while
        nothing arrives — a stop is the one event no put announces."""
        while not until.is_set():
            self._arrived.clear()
            self.drain()
            try:
                job = self._writes.get_nowait()
            except queue.Empty:
                self._arrived.wait(_QUEUE_TICK)
                continue
            job.execute(self._app)

    def abandon(self) -> None:
        """Answer every job still waiting, and every job still to come,
        with the reason there is no answer coming. Called when the loop
        is over: a handler thread is blocked on `run` until its job is
        executed, and nothing will execute it now — including the
        handler of a keep-alive connection that outlives the serving."""
        self._closed = True
        for pending in (self._reads, self._writes):
            while True:
                try:
                    job = pending.get_nowait()
                except queue.Empty:
                    break
                job.error = StoppingError()
                job.done.set()

    def drain(self) -> None:
        """Answer every read waiting right now, and return. Called from
        the app's own thread — the loop between jobs, and a reply
        between frames — so nothing here runs beside anything else: the
        app still sees one caller, in order."""
        while True:
            try:
                job = self._reads.get_nowait()
            except queue.Empty:
                return
            job.execute(self._app)
