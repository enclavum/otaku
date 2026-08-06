"""The migration toolkit: parse-guided textual surgery over the settings
files, and the write machinery every edit rides.

Applicability is decided on the PARSED file (a key mentioned in a
comment or a string can never false-match; a commented-out `# key = …`
counts as absent) while the edit itself is textual — line by line, the
exact rows a change requires, never a re-render: every other byte,
comment, and blank of the file survives. A chained result must parse
back or it is discarded wholesale — a migration bug leaves the file
old, never broken — and the write is atomic, the pre-edit text kept as
a dated backup in configs/backups/ (`-N` appended when the day already
has one).

The textual scan is line-based, which is sound for these files because
they hold no multiline strings by construction; prompts.toml (multiline
templates) must not be edited with these tools.
"""

import os
import re
import tomllib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from otaku.paths import Paths
from otaku.settings.files import write_atomic

# One shape change: config text in, config text out (unchanged when the
# change does not apply).
Migration = Callable[[str], str]

_ANY_HEADER = re.compile(r"\s*\[")


def ensure_section(name: str, block: str, after: str = "") -> Migration:
    """A migration adding a whole new `[name]` section: when the parsed
    file has no such table, `block` — the header line and its rows,
    exactly as a fresh config would render them — is inserted right below
    the `[after]` section, one separating blank line between, so the
    migrated file keeps the fresh file's order. Without `after`, or when
    the file no longer has that section, the block is appended at the end
    instead. Present, even empty: untouched."""

    def apply(text: str) -> str:
        parsed = parse(text)
        if parsed is None or name in parsed:
            return text
        chunk = block if block.endswith("\n") else block + "\n"
        lines = text.splitlines()
        span = _section_span(lines, after) if after else None
        if span is None:
            return text.rstrip("\n") + "\n\n" + chunk
        return joined(_inserted_at_span_end(lines, span, ["", *chunk.splitlines()]))

    return apply


def set_key(section: str, key: str, line: str) -> Migration:
    """A migration replacing the `key = …` line of `[section]` (a literal
    top-level name wins — providers.toml sections carry user-chosen
    names, dots included — else dotted names reach child tables) with
    `line`, the freshly rendered
    `key = value` row: the value is the change, so a trailing comment on
    the old line goes with it, while comment lines above stay. An absent
    key is added at the section's end; no `[section]` in the file means
    no edit. Unlike `ensure_section`, which never touches what exists,
    this imposes the value — the app's explicit edits, through
    `update_providers`."""

    def apply(text: str) -> str:
        parsed = parse(text)
        if parsed is None or not isinstance(_table(parsed, section), dict):
            return text
        lines = text.splitlines()
        span = _section_span(lines, section)
        if span is None:
            return text
        at = _key_index(lines, span, key)
        if at is None:
            return joined(_inserted_at_span_end(lines, span, [line]))
        if lines[at] == line:
            return text
        return joined([*lines[:at], line, *lines[at + 1 :]])

    return apply


def drop_key_everywhere(key: str) -> Migration:
    """A migration removing a retired `key = value` line from every
    top-level section — the sweep a homogeneous file needs
    (providers.toml, whose sections carry the user's own names).
    Comment lines attached directly above each removed line go with it.
    Absent: untouched."""

    def apply(text: str) -> str:
        parsed = parse(text)
        if parsed is None:
            return text
        for name, table in parsed.items():
            if isinstance(table, dict) and key in table:
                text = _dropped(text, name, key)
        return text

    return apply


def apply_migrations(text: str, migrations: list[Migration]) -> str:
    """`text` run through the table, in order. Returns the original text —
    the same object — when nothing changed, or when the chained result no
    longer parses as TOML: a broken migration must never reach the file."""
    migrated = text
    for migration in migrations:
        migrated = migration(migrated)
    if migrated == text or parse(migrated) is None:
        return text
    return migrated


def update_config(paths: Paths, changes: list[Migration]) -> bool:
    """One edit of config.toml, committed — the launch table rides this.
    A missing file is bootstrap's business, and OSError is swallowed: an
    edit is never worth a crash. Returns whether the file changed."""
    try:
        text = paths.config_file.read_text()
    except OSError:
        return False
    migrated = apply_migrations(text, changes)
    if migrated == text:
        return False
    return commit(paths.config_file, backup_path(paths, "config"), text, migrated)


def update_providers(paths: Paths, changes: list[Migration]) -> bool:
    """One edit of providers.toml, committed — the provider moves and the
    model picker's field saves ride this. Same machinery, same guarantees
    as `update_config`. Returns whether the file changed."""
    try:
        text = paths.providers_file.read_text()
    except OSError:
        return False
    migrated = apply_migrations(text, changes)
    if migrated == text:
        return False
    return commit(paths.providers_file, backup_path(paths, "providers"), text, migrated)


# ---------- the write machinery ----------


def backup_path(paths: Paths, stem: str) -> Path:
    """The next free dated backup name: `stem-YYYYMMDD.toml` for the
    day's first edit, `-N` appended for every further one — no edit ever
    overwrites an earlier state."""
    stamp = datetime.now().astimezone().strftime("%Y%m%d")
    path = paths.config_backups_dir / f"{stem}-{stamp}.toml"
    n = 0
    while path.exists():
        n += 1
        path = paths.config_backups_dir / f"{stem}-{stamp}-{n}.toml"
    return path


def commit(file: Path, backup: Path, text: str, migrated: str) -> bool:
    """The write behind every config edit: the pre-edit text kept under
    its own dated backup name — born 0600 in a 0700 backups dir, since a
    pre-seal state may hold a plain api key — then the atomic replace.
    OSError is swallowed — an edit is never worth a crash — and False
    reports it."""
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(backup.parent, 0o700)
        # Born 0600: never a moment (or a crash residue) at umask perms.
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        write_atomic(file, migrated)
    except OSError:
        return False
    return True


# ---------- the textual scan ----------


def parse(text: str) -> dict[str, object] | None:
    """The file as TOML, or None when it does not parse."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def attached_start(lines: list[str], span: tuple[int, int], at: int) -> int:
    """First index of the comment run sitting directly on top of line
    `at` — full-line comments with no blank between, never crossing the
    span's start; `at` itself when there is none."""
    start = at
    while start - 1 > span[0] and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    return start


def joined(lines: list[str]) -> str:
    """The lines back as file text, one trailing newline."""
    return "\n".join(lines) + "\n"


def _table(parsed: dict[str, object], section: str) -> object:
    """The parsed table `section` names: the literal top-level key when
    one exists — a user-named `["my.server"]` — else the dotted walk."""
    literal = parsed.get(section)
    if isinstance(literal, dict):
        return literal
    return _child(parsed, section)


def _child(parsed: dict[str, object], section: str) -> object:
    """The parsed table at a possibly dotted section name, or None."""
    node: object = parsed
    for part in section.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _dropped(text: str, section: str, key: str) -> str:
    """`text` with the `key = …` line of `[section]` removed, attached
    comments included — unchanged when the scan cannot find it."""
    lines = text.splitlines()
    span = _section_span(lines, section)
    if span is None:
        return text
    at = _key_index(lines, span, key)
    if at is None:
        return text
    start = attached_start(lines, span, at)
    return joined(lines[:start] + lines[at + 1 :])


def _section_span(lines: list[str], name: str) -> tuple[int, int] | None:
    """(header index, end index) of `[name]` — end exclusive, at the next
    header or the file's end; None when no such header line exists."""
    quoted = re.escape(f'"{name}"')
    header = re.compile(rf"\s*\[\s*(?:{re.escape(name)}|{quoted})\s*\]\s*(?:#.*)?$")
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


def _inserted_at_span_end(lines: list[str], span: tuple[int, int], new: list[str]) -> list[str]:
    """`new` placed after the section's last non-blank line."""
    header, end = span
    last = end - 1
    while last > header and not lines[last].strip():
        last -= 1
    return lines[: last + 1] + new + lines[last + 1 :]
