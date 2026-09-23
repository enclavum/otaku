"""The watch poller behind `/api/watch`: each file the page is made of
that changed, forever, and a tick where nothing did — polled rather than
watched, because a dependency on a file watcher buys nothing here: a
browser reload is slower than the interval, and the two directories
hold about thirty files between them."""

import contextlib
import time
from collections.abc import Iterator
from pathlib import Path

# How often the files behind the stream are looked at. A second is under
# the time it takes to alt-tab back to the browser, and a stat of thirty
# files costs nothing next to that.
_INTERVAL = 1.0


def changes(*watched: Path) -> Iterator[str | None]:
    """Each file of the page's that changed, forever — written, added or
    removed. A deletion counts: the browser would otherwise keep running
    a script that is no longer there. A tick where nothing changed yields
    None, so the caller can write to its socket often enough to notice a
    reader that has gone.

    Polled rather than watched (the module's docstring says why)."""
    seen = _stamps(watched)
    while True:
        time.sleep(_INTERVAL)
        now = _stamps(watched)
        # The NAME, not where it lives: all the page does with it is tell
        # a stylesheet from everything else, and a server's own paths are
        # nothing to hand a browser.
        moved = now.keys() | seen.keys()
        changed = [path.name for path in moved if now.get(path) != seen.get(path)]
        seen = now
        if not changed:
            yield None
        yield from changed


def _stamps(watched: tuple[Path, ...]) -> dict[Path, float]:
    """Every file under the watched directories and when it was written.
    A file that goes away between the listing and the stat is simply
    absent from this one — an editor saving atomically does exactly
    that, and it must not take the stream down with it. So is a whole
    directory that is not there: the reader's own is absent until they
    make it."""
    stamps: dict[Path, float] = {}
    for directory in watched:
        with contextlib.suppress(OSError):
            for path in directory.rglob("*"):
                with contextlib.suppress(OSError):
                    if path.is_file():
                        stamps[path] = path.stat().st_mtime
    return stamps
