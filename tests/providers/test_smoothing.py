"""The jitter buffer: what goes in comes out whole and in order, the
reasoning at once, the stats last, and the caller's idle hook called
while the wrapper waits."""

import time
from collections.abc import Iterator

from otaku.providers import Chunk, Reasoning, Stats, Text
from otaku.providers.smoothing import smoothen


def _bursty() -> Iterator[Chunk]:
    yield Reasoning("hm")
    yield Text("The light ")
    time.sleep(0.05)
    yield Text("went out, ")
    yield Text("and something stirred.")
    yield Stats(prompt_tokens=7, completion_tokens=5)


class TestSmoothen:
    def test_the_text_arrives_whole_and_in_order(self) -> None:
        chunks = list(smoothen(_bursty()))
        text = "".join(c.text for c in chunks if isinstance(c, Text))
        assert text == "The light went out, and something stirred."

    def test_reasoning_comes_first_and_the_stats_last(self) -> None:
        chunks = list(smoothen(_bursty()))
        assert isinstance(chunks[0], Reasoning) and chunks[0].text == "hm"
        assert isinstance(chunks[-1], Stats)
        assert (chunks[-1].prompt_tokens, chunks[-1].completion_tokens) == (7, 5)
        assert sum(isinstance(c, Stats) for c in chunks) == 1

    def test_the_idle_hook_is_called_while_waiting(self) -> None:
        ticks = 0

        def idle() -> None:
            nonlocal ticks
            ticks += 1

        def slow() -> Iterator[Chunk]:
            time.sleep(0.1)
            yield Text("late")
            yield Stats()

        list(smoothen(slow(), idle))
        assert ticks > 0

    def test_unpaced_the_text_arrives_as_it_comes_and_the_hook_still_ticks(self) -> None:
        # The relay without the jitter buffer: the pump and the tick, for
        # a caller that needs to be ticked and not paced.
        ticks = 0

        def idle() -> None:
            nonlocal ticks
            ticks += 1

        chunks = list(smoothen(_bursty(), idle, paced=False))
        text = "".join(c.text for c in chunks if isinstance(c, Text))
        assert text == "The light went out, and something stirred."
        assert isinstance(chunks[0], Reasoning) and isinstance(chunks[-1], Stats)
        assert ticks > 0

    def test_unpaced_a_hook_saying_nobody_reads_cuts_the_source(self) -> None:
        # The cut is asked the moment the hook says so — long before the
        # source would have answered; the close then waits for the pump
        # to file its answer, which this fake cut cannot hurry.
        cut_at: list[float] = []

        def slow() -> Iterator[Chunk]:
            time.sleep(0.5)
            yield Text("late")
            yield Stats()

        started = time.monotonic()
        assert (
            list(
                smoothen(
                    slow(), lambda: False, lambda: cut_at.append(time.monotonic()), paced=False
                )
            )
            == []
        )
        assert len(cut_at) == 1 and cut_at[0] - started < 0.4

    def test_a_closed_consumer_stops_the_source(self) -> None:
        closed = False

        def endless() -> Iterator[Chunk]:
            nonlocal closed
            try:
                while True:
                    yield Text("x" * 50)
                    time.sleep(0.01)
            finally:
                closed = True

        stream = smoothen(endless())
        next(stream)
        stream.close()
        deadline = time.monotonic() + 2.0
        while not closed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert closed
