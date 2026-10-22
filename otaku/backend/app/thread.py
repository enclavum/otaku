"""The app's own thread: every call of the interface runs on it, one at a
time, whichever thread makes it.

The app is used by one thread at a time — the store's main connection and
the app's caches hold no lock — so from the end of the launch one thread
owns it, and every caller (a frontend's main thread, a page's request
threads) hands it work and waits for the answer: serialization comes
free. A call made on that thread runs where it is, and so does one made
before the thread starts, the launch's own.

TWO lanes, and the difference between them is what a job may do. A write
moves the story, so it waits its turn behind whatever is running — a
reply, most of the time, which is minutes of model. A READ moves nothing:
it is answered in the gaps of the job running — between a reply's events,
and while a job waits on the model (`idle`) — or every screen would be
dead until the model stops talking.

A caller may give up on its job (a Ctrl+C, a page gone): one still queued
is dropped, one running is told through `idle`, which a wait on the model
asks many times a second, and a reply between its events. A reply is one
job from its line's acceptance to its last event, the events handed
across as they come (`stream`)."""

import queue
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from otaku.backend.errors import StoppingError

T = TypeVar("T")
E = TypeVar("E")

# How long the thread sleeps before looking up when nothing has arrived —
# short enough that a close is felt, and never the latency of a call,
# which wakes the wait itself.
_QUEUE_TICK = 0.25
# How long a stream's reader waits before looking at its job again: what
# ends a stream without a word (its job dropped, or answered StoppingError)
# is seen this late at most.
_HANDOFF_TICK = 0.1
# How long a close waits out the job in hand. A cancelled reply ends at its
# next tick; a job still running past this is left to the process's exit.
_CLOSE_WAIT = 10.0


@dataclass
class _Work:
    """One call waiting for the app's thread, its answer coming back — a
    value or what it raised — and whether its caller gave up on it."""

    call: Callable[[], Any]
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    value: Any = None
    error: BaseException | None = None
    # Between the thread taking the job and its caller giving up on it:
    # one of the two wins, never both.
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def claim(self) -> bool:
        """The thread takes the job; False when its caller gave up first."""
        with self._lock:
            if self.cancelled.is_set():
                return False
            self.started = True
            return True

    def cancel(self) -> bool:
        """The caller gives up on the job; whether it had started, and so
        must be waited out."""
        with self._lock:
            self.cancelled.set()
            return self.started

    def execute(self) -> None:
        try:
            self.value = self.call()
        except BaseException as e:  # carried to the caller, never swallowed
            self.error = e
        finally:
            self.done.set()

    def wait(self) -> None:
        """Wait for the answer. A Ctrl+C in the wait gives up on the job:
        dropped when queued, waited out when running — a reply ends at its
        next tick — then the interrupt goes on up."""
        try:
            self.done.wait()
        except KeyboardInterrupt:
            if self.cancel():
                self.done.wait()
            raise


class AppThread:
    """The app's own thread, and the queue between it and its callers."""

    def __init__(self) -> None:
        self._writes: queue.Queue[_Work] = queue.Queue()
        self._reads: queue.Queue[_Work] = queue.Queue()
        # Set by every put, so an idle loop wakes on the work rather than
        # on the clock.
        self._arrived = threading.Event()
        self._closed = False
        self._os_thread: threading.Thread | None = None
        # The job the thread took from the write lane, for `idle` to ask.
        self._current: _Work | None = None

    def start(self) -> None:
        """Take the app over: its calls run on the thread started here
        from now on."""
        self._os_thread = threading.Thread(target=self._loop, name="otaku-app", daemon=True)
        self._os_thread.start()

    def run(self, call: Callable[[], T], *, reading: bool = False) -> T:
        """`call` on the app's thread; what it raises is raised again here.
        `reading` promises it only reads, so it may run between a reply's
        events."""
        if self._is_inline():
            return call()
        job = _Work(call)
        self._put(job, reading=reading)
        job.wait()
        if job.error is not None:
            raise job.error
        return job.value  # type: ignore[no-any-return]

    def stream(self, start: Callable[[], Iterator[E]]) -> Iterator[E]:
        """A reply: `start` called on the app's thread — refusing there,
        before any event, as it would on the caller's — then its events
        handed across as they come, the thread held from the first to the
        last. Closing the answer cancels the reply and waits it out."""
        if self._is_inline():
            return start()
        handoff: _Stream[E] = _Stream(self, start)
        self._put(handoff.job, reading=False)
        handoff.accept()
        return handoff

    def idle(self) -> bool:
        """What a job waiting on the model does with the time: the reads
        queued meanwhile answered. False once its caller has given up on
        it, which ends the wait — the request cut, what arrived kept."""
        self.drain()
        current = self._current
        return current is None or not current.cancelled.is_set()

    def drain(self) -> None:
        """Answer every read waiting now, on the app's thread alone — the
        loop between jobs, a reply between its events, a wait on the model
        — so the app still sees one caller at a time."""
        if not self._is_inline():
            return
        while True:
            try:
                job = self._reads.get_nowait()
            except queue.Empty:
                return
            if job.claim():
                job.execute()

    def close(self) -> None:
        """Stop: the job in hand given up on and waited out, every job
        still queued — and every one to come — answered StoppingError."""
        self._closed = True
        current = self._current
        if current is not None:
            current.cancel()
        self._arrived.set()
        if self._os_thread is not None and not self._is_inline():
            self._os_thread.join(_CLOSE_WAIT)
        self._abandon()

    def _is_inline(self) -> bool:
        """Whether a call runs where it is made: on the app's thread, or
        before there is one."""
        return self._os_thread is None or threading.current_thread() is self._os_thread

    def _put(self, job: _Work, *, reading: bool) -> None:
        if self._closed:
            raise StoppingError
        (self._reads if reading else self._writes).put(job)
        self._arrived.set()
        if self._closed:
            # `_abandon` may have swept the queues between the check above
            # and the put: nothing will run this job, so it is answered
            # here. Both answering is harmless — same error, `done` twice.
            job.error = StoppingError()
            job.done.set()

    def _loop(self) -> None:
        """Serve both lanes until the close, finishing the job in hand.
        The flag is CLEARED before the queues are looked at, never after:
        a job arriving between the look and the wait sets it again."""
        while not self._closed:
            self._arrived.clear()
            self.drain()
            try:
                job = self._writes.get_nowait()
            except queue.Empty:
                self._arrived.wait(_QUEUE_TICK)
                continue
            if job.claim():
                self._current = job
                try:
                    job.execute()
                finally:
                    self._current = None
        self._abandon()

    def _abandon(self) -> None:
        """Answer every job still waiting with the reason none is coming."""
        self._closed = True
        for pending in (self._reads, self._writes):
            while True:
                try:
                    job = pending.get_nowait()
                except queue.Empty:
                    break
                job.error = StoppingError()
                job.done.set()


# What a stream's job hands across besides its events.
_ACCEPTED = object()
_END = object()


@dataclass(frozen=True)
class _Raised:
    """What the reply raised, for its reader to raise."""

    error: BaseException


class _Stream(Generic[E]):
    """A reply as its caller reads it: the events its job hands across,
    in order. `close` is the cancel — the same door a closed generator is,
    a reply ended with what arrived kept — and returns once the job is
    over, so the kept reply is recorded before the caller draws."""

    def __init__(self, thread: AppThread, start: Callable[[], Iterator[E]]) -> None:
        self._app_thread = thread
        self._handoff: queue.Queue[Any] = queue.Queue()
        self.job = _Work(lambda: self._pump(start))
        self._closed = False

    def __iter__(self) -> "_Stream[E]":
        return self

    def __next__(self) -> E:
        if self._closed:
            raise StopIteration
        item = self._take()
        if item is _END:
            self._closed = True
            raise StopIteration
        if isinstance(item, _Raised):
            self._closed = True
            raise item.error
        return item  # type: ignore[no-any-return]

    def accept(self) -> None:
        """Wait for the line's acceptance; what refused it is raised."""
        item = self._take()
        if isinstance(item, _Raised):
            self._closed = True
            raise item.error

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.job.cancel():
            self.job.done.wait()

    def _take(self) -> Any:
        """The next thing the job handed across. A job that ended without
        a word — dropped, or answered StoppingError — ends the stream; a
        Ctrl+C in the wait cancels it, then goes on up."""
        try:
            while True:
                try:
                    return self._handoff.get(timeout=_HANDOFF_TICK)
                except queue.Empty:
                    if self.job.done.is_set() and self._handoff.empty():
                        error = self.job.error or StoppingError()
                        return _Raised(error)
        except KeyboardInterrupt:
            self.close()
            raise

    def _pump(self, start: Callable[[], Iterator[E]]) -> None:
        """The job, on the app's thread: the reply started, then its events
        handed across one by one, the reads waiting answered between them;
        whatever ends it, the reply is closed — its cancel-and-keep when
        it ends early."""
        try:
            events = start()
        except BaseException as e:
            self._handoff.put(_Raised(e))
            return
        self._handoff.put(_ACCEPTED)
        try:
            # The flag is looked at BEFORE each event is asked for: the
            # first records the line, so a reply given up on before it is
            # never played.
            while not self.job.cancelled.is_set():
                try:
                    event = next(events)
                except StopIteration:
                    break
                self._handoff.put(event)
                self._app_thread.drain()
        except BaseException as e:
            self._handoff.put(_Raised(e))
        finally:
            close = getattr(events, "close", None)
            if close is not None:
                close()
            self._handoff.put(_END)
