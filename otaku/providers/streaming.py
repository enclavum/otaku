"""Stream smoothing: re-time bursty model output into an even flow.

Some servers merge tokens before flushing, so their stream arrives in
bursts — chunky typing even when the generation rate is fine. `smooth` is a
jitter buffer: a pump thread drains the source stream at full speed (so the
final `Stats` timing stays real) while the wrapper emits characters at the
stream's own measured arrival rate, holding roughly _LAG to 2x _LAG seconds
of text. Below that band it decelerates proportionally instead of stalling;
above it, it catches up boundedly — so the held lag self-stabilizes, which
is the classic jitter-buffer tradeoff: smoothness beyond the held lag is
impossible without more lag.

`Thinking` deltas pass through immediately; `Stats` (or a relayed error)
arrives after the buffered text has fully drained. The pump checks a done
flag on every chunk and closes the source stream itself, so cancelling the
consumer still stops the server's generation promptly.
"""

from __future__ import annotations  # `Chunk` is imported for typing only

import threading
import time
from collections import deque
from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from otaku.providers.base import Chunk

_LAG = 0.25  # target display lag; flush gaps up to ~2x this are absorbed fully
_RATE_WINDOW = 3.0  # sliding window (seconds) for the arrival-rate estimate
_TICK = 0.02  # emit cadence


def smooth(chunks: Iterator[Chunk]) -> Iterator[Chunk]:
    from otaku.providers.base import Stats, Text, Thinking

    buffer: list[str] = []
    thinking: deque[Thinking] = deque()
    lock = threading.Lock()
    done = threading.Event()
    final: list[Stats | None] = [None]
    error: list[Exception | None] = [None]

    def pump() -> None:
        try:
            for chunk in chunks:
                if isinstance(chunk, Text):
                    with lock:
                        buffer.extend(chunk.text)
                elif isinstance(chunk, Thinking):
                    with lock:
                        thinking.append(chunk)
                elif isinstance(chunk, Stats):
                    final[0] = chunk
                if done.is_set():  # consumer aborted — stop reading
                    break
        except Exception as e:  # relayed to the consumer after draining
            error[0] = e
        finally:
            closer = getattr(chunks, "close", None)
            if closer is not None:
                closer()
            done.set()

    worker = threading.Thread(target=pump, name="otaku-smooth", daemon=True)
    worker.start()
    emitted = 0
    carry = 0.0
    samples: deque[tuple[float, int]] = deque()  # (time, chars arrived by then)
    last = time.monotonic()
    try:
        while True:
            out_thinking: Thinking | None = None
            out_text = ""
            now = time.monotonic()
            elapsed, last = now - last, now
            with lock:
                if thinking:
                    out_thinking = thinking.popleft()
                elif buffer:
                    arrived = emitted + len(buffer)
                    if not samples:
                        samples.append((now - elapsed, 0))
                    samples.append((now, arrived))
                    while len(samples) > 2 and samples[0][0] < now - _RATE_WINDOW:
                        samples.popleft()
                    t0, c0 = samples[0]
                    rate = (arrived - c0) / max(now - t0, 1e-9)
                    want = _pace(len(buffer), rate) * elapsed + carry
                    n = min(int(want), len(buffer))
                    carry = min(want - n, 1.0)
                    if done.is_set() and not n:
                        n, carry = 1, 0.0  # stream over — never let the tail crawl
                    out_text = "".join(buffer[:n])
                    del buffer[:n]
                    emitted += n
                finished = done.is_set() and not buffer and not thinking
            if out_thinking is not None:
                yield out_thinking
                continue
            if out_text:
                yield Text(out_text)
            if finished:
                break
            time.sleep(_TICK)
        if error[0] is not None:
            raise error[0]
        if final[0] is not None:
            yield final[0]
    finally:
        done.set()  # stop the pump if the consumer aborted mid-stream


def _pace(backlog: int, rate: float) -> float:
    """Characters per second to emit this tick: the arrival rate while the
    buffer holds 1-2x _LAG of text (the steady regime), proportionally slower
    as it runs low, boundedly faster when the producer surges ahead."""
    return min(max(rate, backlog / (2 * _LAG)), backlog / _LAG)
