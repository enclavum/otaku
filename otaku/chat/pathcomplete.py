"""Filesystem path completion for command arguments.

Any command whose completion subtree reaches `PATH_LEAF` (see
`chat.commands`) gets this behavior at its path argument, through the one
entry point `completions`. The slash completer owns WHEN to complete —
behind an explicit `@`, where the menu pops immediately and filters
while typing — and the raw-line slicing that lets spaces survive
anywhere in a path; this module owns WHAT a path prefix completes to:

- only the last segment (after the final `/`) is completed, so `~/` and
  the directories already typed stay exactly as written;
- hidden entries are offered only when the fragment itself starts with a
  dot; directories show with a trailing `/`; names sort casefolded and
  display at one width, so the menu never resizes while filtering.

`split` and `matches` are the pure core — the disk stays out of them.
"""

from collections.abc import Iterable, Iterator
from pathlib import Path

from prompt_toolkit.completion import Completion


def completions(prefix: str) -> Iterator[Completion]:
    """The menu rows for a partly typed path: `matches` over the entries
    of the directory `prefix` sits in (`~` expanded for the listing only;
    an unreadable directory offers nothing)."""
    base, fragment = split(prefix)
    directory = Path(base).expanduser() if base else Path(".")
    try:
        entries = [(entry.name, entry.is_dir()) for entry in directory.iterdir()]
    except OSError:
        return
    names = matches(entries, fragment)
    width = max((len(name) for name in names), default=0)
    for name in names:
        yield Completion(name, start_position=-len(fragment), display=name.ljust(width))


def split(prefix: str) -> tuple[str, str]:
    """(directory part, fragment) of a partly typed path: everything
    through the final `/` — kept exactly as typed, `~` unexpanded, spaces
    intact — and the segment being completed after it. No `/` yet:
    ("", prefix)."""
    at = prefix.rfind("/")
    if at < 0:
        return "", prefix
    return prefix[: at + 1], prefix[at + 1 :]


def matches(entries: Iterable[tuple[str, bool]], fragment: str) -> list[str]:
    """Display names among `entries` — (name, is_dir) pairs — completing
    `fragment`: prefix-matched, hidden entries only when the fragment
    itself starts with a dot, directories with a trailing `/`, ordered by
    casefolded name."""
    hidden_wanted = fragment.startswith(".")
    return [
        name + ("/" if is_dir else "")
        for name, is_dir in sorted(entries, key=lambda entry: entry[0].casefold())
        if name.startswith(fragment) and (hidden_wanted or not name.startswith("."))
    ]
