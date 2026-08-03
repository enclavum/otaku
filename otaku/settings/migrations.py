"""Config migrations: the one way the app edits a user-owned settings file.

config.toml belongs to the user, so a migration edits it surgically — the
exact lines a shape change requires, never a re-render: every other byte,
comment, and blank of the file survives. `_migrations` is the ordered
table, one entry per shape change across app versions, built from the
four factories below. Every entry is idempotent and the table convergent,
so it simply runs at every launch — no version stamp to trust, or to lose
when a config file travels between machines — and the file is written
only when something actually changed.

Applicability is decided on the PARSED file (a key mentioned in a comment
or a string can never false-match; a commented-out `# key = …` counts as
absent) while the edit itself is textual. The chained result must parse
back or it is discarded wholesale — a migration bug leaves the file old,
never broken — and the write is atomic, with the pre-migration text kept
as configs/backups/config-YYYYMMDD.toml (the day's first state wins, like
the database snapshots).

The textual scan is line-based, which is sound for config.toml because it
holds no multiline strings by construction; prompts.toml (multiline
templates) must not be migrated with these tools.
"""

import re
import tomllib
from collections.abc import Callable
from datetime import datetime

from otaku.paths import Paths
from otaku.settings.files import write_atomic

# One shape change: config text in, config text out (unchanged when the
# change does not apply).
Migration = Callable[[str], str]

_ANY_HEADER = re.compile(r"\s*\[")


def _migrations() -> list[Migration]:
    """The shape-change table, oldest first: one factory call per change
    across app versions, each safe to re-run on any config the app ever
    wrote. A function, not a constant, only so the table can sit here on
    top — its entries call the factories defined below."""
    return []


def ensure_section(name: str, block: str) -> Migration:
    """A migration adding a whole new `[name]` section: when the parsed
    file has no such table, `block` — the header line and its rows,
    exactly as a fresh config would render them — is appended at the end
    of the file after one separating blank line. Present, even empty:
    untouched."""

    def apply(text: str) -> str:
        parsed = _parse(text)
        if parsed is None or name in parsed:
            return text
        chunk = block if block.endswith("\n") else block + "\n"
        return text.rstrip("\n") + "\n\n" + chunk

    return apply


def ensure_key(section: str, key: str, line: str) -> Migration:
    """A migration adding `line` (a `row(...)`-rendered `key = value`) to
    an existing `[section]`: inserted after the section's last non-blank
    line when the parsed table lacks `key`. No `[section]` in the file —
    the user deleted it — means no insertion: the loader's default serves
    them."""

    def apply(text: str) -> str:
        parsed = _parse(text)
        if parsed is None:
            return text
        table = parsed.get(section)
        if not isinstance(table, dict) or key in table:
            return text
        lines = text.splitlines()
        span = _section_span(lines, section)
        if span is None:
            return text
        return _joined(_inserted_at_span_end(lines, span, [line]))

    return apply


def move_key(old: str, new: str, key: str) -> Migration:
    """A migration relocating `key` from `[old]` to `[new]`: the user's
    own line — their value, their trailing comment, any comment lines
    attached directly above — moves verbatim to the end of `[new]`, which
    is created (after one blank line, at the file's end) when missing.
    With the key already present in `[new]`, the `[old]` copy is simply
    dropped: the new location wins. Nothing in `[old]`: untouched."""

    def apply(text: str) -> str:
        parsed = _parse(text)
        if parsed is None:
            return text
        old_table = parsed.get(old)
        if not isinstance(old_table, dict) or key not in old_table:
            return text
        lines = text.splitlines()
        span = _section_span(lines, old)
        if span is None:
            return text
        at = _key_index(lines, span, key)
        if at is None:
            return text
        start = _attached_start(lines, span, at)
        moved, lines = lines[start : at + 1], lines[:start] + lines[at + 1 :]
        new_table = parsed.get(new)
        if isinstance(new_table, dict) and key in new_table:
            return _joined(lines)  # already at the destination
        span = _section_span(lines, new)
        if span is None:
            while lines and not lines[-1].strip():
                lines.pop()
            return _joined([*lines, "", f"[{new}]", *moved])
        return _joined(_inserted_at_span_end(lines, span, moved))

    return apply


def drop_key(section: str, key: str) -> Migration:
    """A migration removing a retired `key = value` line from `[section]`,
    along with any comment lines attached directly above it — the way a
    human would take the pair out. Absent: untouched."""

    def apply(text: str) -> str:
        parsed = _parse(text)
        if parsed is None:
            return text
        table = parsed.get(section)
        if not isinstance(table, dict) or key not in table:
            return text
        lines = text.splitlines()
        span = _section_span(lines, section)
        if span is None:
            return text
        at = _key_index(lines, span, key)
        if at is None:
            return text
        start = _attached_start(lines, span, at)
        return _joined(lines[:start] + lines[at + 1 :])

    return apply


def apply_migrations(text: str, migrations: list[Migration]) -> str:
    """`text` run through the table, in order. Returns the original text —
    the same object — when nothing changed, or when the chained result no
    longer parses as TOML: a broken migration must never reach the file."""
    migrated = text
    for migration in migrations:
        migrated = migration(migrated)
    if migrated == text or _parse(migrated) is None:
        return text
    return migrated


def migrate(paths: Paths) -> None:
    """The launch step over configs/config.toml: apply the table, and
    when it changed the file, keep the pre-migration text as
    configs/backups/config-YYYYMMDD.toml and write the result atomically.
    A missing config is bootstrap's business, and any OSError is
    swallowed — a migration is never worth a launch."""
    try:
        text = paths.config_file.read_text()
    except OSError:
        return
    migrated = apply_migrations(text, _migrations())
    if migrated == text:
        return
    try:
        stamp = datetime.now().astimezone().strftime("%Y%m%d")
        backup = paths.config_backups_dir / f"config-{stamp}.toml"
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(text)
        write_atomic(paths.config_file, migrated)
    except OSError:
        return


# ---------- the textual scan ----------


def _parse(text: str) -> dict[str, object] | None:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _section_span(lines: list[str], name: str) -> tuple[int, int] | None:
    """(header index, end index) of `[name]` — end exclusive, at the next
    header or the file's end; None when no such header line exists."""
    header = re.compile(rf"\s*\[\s*{re.escape(name)}\s*\]\s*(?:#.*)?$")
    for i, line in enumerate(lines):
        if header.match(line):
            end = i + 1
            while end < len(lines) and not _ANY_HEADER.match(lines[end]):
                end += 1
            return i, end
    return None


def _key_index(lines: list[str], span: tuple[int, int], key: str) -> int | None:
    """Index of the `key = …` line within the section span."""
    pattern = re.compile(rf"\s*{re.escape(key)}\s*=")
    start, end = span
    for i in range(start + 1, end):
        if pattern.match(lines[i]):
            return i
    return None


def _attached_start(lines: list[str], span: tuple[int, int], at: int) -> int:
    """First index of the comment run sitting directly on top of line
    `at` — full-line comments with no blank between; `at` itself when
    there is none."""
    start = at
    while start - 1 > span[0] and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    return start


def _inserted_at_span_end(lines: list[str], span: tuple[int, int], new: list[str]) -> list[str]:
    """`new` placed after the section's last non-blank line."""
    header, end = span
    last = end - 1
    while last > header and not lines[last].strip():
        last -= 1
    return lines[: last + 1] + new + lines[last + 1 :]


def _joined(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"
