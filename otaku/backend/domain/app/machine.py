"""The machine's memory, from the platform's own numbers.

Both pickers carry a RAM gauge — loading a model is the one thing otaku
does that can fill a machine up — so the reading lives below them, here,
and the sentence they draw it as is `get_machine()`: one machine fact, said
the same way in a terminal and in a page. That is the whole need:
`virtual_memory()` answers `(used, total)` in bytes, or None where
the platform will not say, so the caller hides the line instead of
printing a guess. No dependency for it: the total comes from sysconf,
and what is used from `/proc/meminfo` on Linux and a `vm_stat` parse on
macOS. Windows has neither, so the gauge simply does not appear — the
same degrade-to-silence posture the terminal queries in `tty` take.

USED means what a model loading right now could not have without the
machine going to work for it first. Both platforms are asked that one
question, each in its own vocabulary:

- Linux answers it directly. `MemAvailable` is the kernel's own estimate
  of what a new process could take — free memory plus the page cache it
  would drop for it, less the reserve the kernel keeps back — so used is
  everything else.
- macOS keeps no such number, so used is composed the way Activity
  Monitor composes it: the anonymous pages minus the discardable
  (purgeable) ones, plus the wired and the compressed. Cold anonymous
  memory counts as USED even though the kernel could reclaim it, because
  reclaiming it means compressing or swapping it first — counting the
  whole inactive list as free reads gigabytes rosier than every other
  tool on the machine.

The answer is cached for a couple of seconds: the picker repaints on
every keystroke and every spinner frame, `vm_stat` is a subprocess, and
RAM does not move that fast.
"""

import os
import re
import subprocess
import time

_MEMINFO = "/proc/meminfo"  # its presence is also how Linux is recognized
_VM_STAT = "/usr/bin/vm_stat"

_TTL = 2.0  # seconds; the picker repaints far more often than RAM moves

_PAGE_SIZE = re.compile(r"page size of (\d+) bytes")
_PAGE_ROW = re.compile(r"^(\w[\w\- ]*):\s+(\d+)\.", re.MULTILINE)
# What macOS counts as used, in `vm_stat`'s own row names, less what is
# anonymous but discardable — purgeable pages go with the file cache.
_USED_PAGES = ("Anonymous pages", "Pages wired down", "Pages occupied by compressor")
_DISCARDABLE_PAGES = "Pages purgeable"

_cache: tuple[float, tuple[int, int] | None] | None = None


def get_machine() -> str:
    """The gauge as both pickers print it — "RAM: 12.4 / 32.0 GB (39%)"
    — or "" where the platform will not say, which is a line neither of
    them draws rather than a guess either of them prints."""
    memory = virtual_memory()
    if memory is None:
        return ""
    used, total = memory
    return f"RAM: {used / 1024**3:.1f} / {total / 1024**3:.1f} GB ({100 * used / total:.0f}%)"


def virtual_memory() -> tuple[int, int] | None:
    """`(used, total)` in bytes, or None where the platform will not say.
    Never raises: a gauge is not worth a traceback."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _TTL:
        return _cache[1]
    try:
        memory = _read()
    except Exception:
        memory = None
    _cache = (now, memory)
    return memory


def _read() -> tuple[int, int] | None:
    # os.sysconf itself is absent on Windows — the AttributeError is the
    # platform answering, and `virtual_memory` turns it into None.
    total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    if total <= 0:
        return None
    used = _linux_used(total) if os.path.exists(_MEMINFO) else _macos_used()
    if used is None:
        return None
    return min(used, total), total


def _linux_used(total: int) -> int | None:
    """Everything `MemAvailable` does not cover — the kernel's own
    estimate, present since 3.14, in kilobytes despite the `kB`
    spelling."""
    with open(_MEMINFO) as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return total - min(int(line.split()[1]) * 1024, total)
    return None


def _macos_used() -> int | None:
    """A `vm_stat` parse: the page size from its own header (authoritative
    for the counts below it), then Activity Monitor's own composition —
    anonymous less purgeable, plus wired, plus compressed."""
    result = subprocess.run([_VM_STAT], capture_output=True, text=True, timeout=1, check=False)
    size = _PAGE_SIZE.search(result.stdout)
    if size is None:
        return None
    pages = {name: int(count) for name, count in _PAGE_ROW.findall(result.stdout)}
    if not all(name in pages for name in (*_USED_PAGES, _DISCARDABLE_PAGES)):
        return None
    used = sum(pages[name] for name in _USED_PAGES) - pages[_DISCARDABLE_PAGES]
    return int(size.group(1)) * max(0, used)
