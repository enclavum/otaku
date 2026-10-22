"""The app's own thread (`backend.app.thread`): a call runs on it whichever
thread makes it — where it is made before the thread starts and on the
thread itself; a read is answered while a write waits on the model; a
caller may give up on its job; a reply is one job whose events are
handed across, closed to cancel it; and a closed thread answers every
call StoppingError."""

import signal
import threading
import time
from collections.abc import Callable, Iterator

import pytest

from otaku.backend.app.thread import AppThread
from otaku.backend.errors import StoppingError


@pytest.fixture
def thread() -> Iterator[AppThread]:
    running = AppThread()
    running.start()
    yield running
    running.close()


class TestCalls:
    def test_a_call_before_the_start_runs_where_it_is_made(self) -> None:
        assert AppThread().run(threading.current_thread) is threading.current_thread()

    def test_a_call_runs_on_the_apps_thread(self, thread: AppThread) -> None:
        assert thread.run(threading.current_thread) is not threading.current_thread()

    def test_what_a_call_raises_is_raised_at_its_caller(self, thread: AppThread) -> None:
        with pytest.raises(ZeroDivisionError):
            thread.run(lambda: 1 / 0)

    def test_a_call_made_on_the_apps_thread_runs_where_it_is(self, thread: AppThread) -> None:
        # a unit that calls the interface again does not wait on itself
        assert thread.run(lambda: thread.run(lambda: 42)) == 42


class TestLanes:
    def test_a_read_is_answered_while_a_write_waits_on_the_model(self, thread: AppThread) -> None:
        waiting, answered = threading.Event(), threading.Event()

        def write() -> None:
            waiting.set()
            while not answered.wait(0.01):  # the model's wait, asking the hook
                thread.idle()

        writer = _beside(lambda: thread.run(write))
        assert waiting.wait(5)
        _beside(lambda: thread.run(answered.set, reading=True)).join(5)
        writer.join(5)
        assert answered.is_set() and not writer.is_alive()

    def test_a_write_waits_for_the_one_running(self, thread: AppThread) -> None:
        holding, release, done = threading.Event(), threading.Event(), []
        _beside(lambda: thread.run(lambda: (holding.set(), release.wait(5))))
        assert holding.wait(5)
        second = _beside(lambda: thread.run(lambda: done.append(True)))
        second.join(0.2)
        assert done == []  # still waiting its turn
        release.set()
        second.join(5)
        assert done == [True]


class TestGivingUp:
    def test_a_call_given_up_on_while_queued_never_runs(self, thread: AppThread) -> None:
        holding, release, ran = threading.Event(), threading.Event(), []
        holder = _beside(lambda: thread.run(lambda: (holding.set(), release.wait(5))))
        assert holding.wait(5)
        # Ctrl+C, as the kernel sends it, while this thread waits its turn
        main = threading.main_thread().ident
        assert main is not None
        timer = threading.Timer(0.2, signal.pthread_kill, (main, signal.SIGINT))
        timer.start()
        with pytest.raises(KeyboardInterrupt):
            thread.run(lambda: ran.append(True))
        release.set()
        holder.join(5)
        thread.run(lambda: None)  # the queue has moved past it
        assert ran == []

    def test_the_idle_hook_answers_false_once_a_reply_is_closed(self, thread: AppThread) -> None:
        cut = threading.Event()

        def reply() -> Iterator[str]:
            yield "first"
            while thread.idle():  # the model's wait
                time.sleep(0.01)
            cut.set()
            yield "never read"

        events = thread.stream(reply)
        assert next(events) == "first"
        events.close()  # type: ignore[attr-defined]
        assert cut.is_set()  # over before close returned


class TestStreams:
    def test_a_reply_hands_its_events_across_in_order(self, thread: AppThread) -> None:
        assert list(thread.stream(lambda: iter([1, 2, 3]))) == [1, 2, 3]

    def test_a_refusal_raises_at_the_call_before_any_event(self, thread: AppThread) -> None:
        def refused() -> Iterator[int]:
            raise ValueError("refused")

        with pytest.raises(ValueError):
            thread.stream(refused)

    def test_what_a_reply_raises_midway_is_raised_at_its_reader(self, thread: AppThread) -> None:
        def failing() -> Iterator[int]:
            yield 1
            raise ValueError("failed")

        events = thread.stream(failing)
        assert next(events) == 1
        with pytest.raises(ValueError):
            next(events)

    def test_closing_a_reply_midway_closes_it_on_the_apps_thread(self, thread: AppThread) -> None:
        # the cancel-and-keep door: the reply's own generator closed
        closed_on: list[threading.Thread] = []

        def reply() -> Iterator[int]:
            try:
                for i in range(100):
                    time.sleep(0.01)
                    yield i
            except GeneratorExit:
                closed_on.append(threading.current_thread())
                raise

        events = thread.stream(reply)
        assert next(events) == 0
        events.close()  # type: ignore[attr-defined]
        assert len(closed_on) == 1 and closed_on[0] is not threading.current_thread()


class TestClose:
    def test_a_call_after_the_close_is_answered_stopping(self) -> None:
        closed = AppThread()
        closed.start()
        closed.close()
        with pytest.raises(StoppingError):
            closed.run(lambda: None)

    def test_the_close_cancels_the_job_in_hand_and_answers_the_queued(self) -> None:
        closing = AppThread()
        closing.start()
        waiting = threading.Event()

        def wait_on_the_model() -> None:
            waiting.set()
            while closing.idle():
                time.sleep(0.01)

        answers: list[BaseException] = []
        _beside(lambda: closing.run(wait_on_the_model))
        assert waiting.wait(5)
        queued = _beside(lambda: _caught(answers, lambda: closing.run(lambda: None)))
        time.sleep(0.1)  # queued behind the wait
        closing.close()
        queued.join(5)
        assert [type(e) for e in answers] == [StoppingError]


def _beside(call: Callable[[], object]) -> threading.Thread:
    """`call` on a thread of its own, started; a daemon, so a hang fails the
    test rather than the run."""
    running = threading.Thread(target=call, daemon=True)
    running.start()
    return running


def _caught(into: list[BaseException], call: Callable[[], object]) -> None:
    try:
        call()
    except BaseException as e:
        into.append(e)
