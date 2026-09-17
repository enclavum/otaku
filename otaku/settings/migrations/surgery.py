"""The migration toolkit: parse-guided textual surgery over the settings
files, and the write machinery every edit rides.

Applicability is decided on the PARSED file (a key mentioned in a
comment or a string can never false-match; a commented-out `# key = …`
counts as absent) while the edit itself is textual — line by line, the
exact rows a change requires, never a re-render: every other byte,
comment, and blank of the file survives. A chained result must parse
back or it is discarded wholesale — a migration bug leaves the file
old, never broken — and the write is atomic, the pre-edit text kept as
a dated backup (`-N` appended when the day already has one).

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

from otaku.formatting import decode_text
from otaku.settings import write_atomic

# The keys whose values are secrets wherever they appear — a provider's
# api key, the web password — and so never kept in a backup once an
# edit has replaced them (`redacted`).
_SECRETS = ("api_key", "password")

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


def ensure_key(section: str, key: str, line: str, *, after: str | None = None) -> Migration:
    """A migration adding `line` — a freshly rendered `key = value` row —
    to `[section]`, for a file that has the section but not the key: a
    setting that arrived after this config was written, so the whole
    surface stays discoverable in the file.

    `after` names the key it belongs behind, which is how a migrated file
    comes to read the same way down as a freshly rendered one — so it is
    the neighbour `to_toml` puts above it, and a step that skips it is a
    step that shuffles somebody's config. None means the section's head.
    A named key the file does not have puts the row at the section's end,
    which is where a key rendered after an optional one belongs anyway.

    `ensure_section`'s posture one level down — what EXISTS is never
    touched, whatever its value, which is what separates a new default
    from `set_key`'s imposed one. Its `after` names a section and falls
    back to the file's end; this one names a key and falls back to the
    section's head, because a section is appended to a file while a key
    has a place in an order."""

    def apply(text: str) -> str:
        parsed = parse(text)
        if parsed is None or not isinstance(_table(parsed, section), dict):
            return text
        lines = text.splitlines()
        span = _section_span(lines, section)
        if span is None or _key_index(lines, span, key) is not None:
            return text
        if after is None:
            return joined(_inserted_at_span_start(lines, span, [line]))
        neighbour = _key_index(lines, span, after)
        if neighbour is None:
            return joined(_inserted_at_span_end(lines, span, [line]))
        return joined([*lines[: neighbour + 1], line, *lines[neighbour + 1 :]])

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


def rename_section(old: str, new: str) -> Migration:
    """A migration renaming the `[old]` header to `[new]` and touching
    nothing under it: a section that has been called something else,
    whose contents never changed. `rename_key`'s posture one level up —
    every value, comment and blank the reader has in there survives,
    because only the header line is rewritten.

    A file already holding `[new]`, or without `[old]`, is left alone;
    so is a header spelled in a way this cannot rewrite literally (a
    quoted or spaced-out name), which is a file no otaku ever wrote."""

    def apply(text: str) -> str:
        parsed = parse(text)
        if parsed is None or new in parsed or old not in parsed:
            return text
        lines = text.splitlines()
        span = _section_span(lines, old)
        if span is None or f"[{old}]" not in lines[span[0]]:
            return text
        lines[span[0]] = lines[span[0]].replace(f"[{old}]", f"[{new}]", 1)
        return joined(lines)

    return apply


def rename_key(section: str, old: str, new: str, render: Callable[[object], str]) -> Migration:
    """A migration renaming `[section]`'s `old = value` key to `new`, the
    value carried over: the old line is replaced in place by
    `render(value)` — the freshly rendered `new = value` row, comment
    included — so a hand-set value survives its setting's rename. A file
    already holding `new`, or without `old`, is untouched; a render that
    produces invalid TOML is discarded by `apply_migrations`' parse
    check, never written."""

    def apply(text: str) -> str:
        parsed = parse(text)
        table = _table(parsed, section) if parsed is not None else None
        if not isinstance(table, dict) or old not in table or new in table:
            return text
        lines = text.splitlines()
        span = _section_span(lines, section)
        if span is None:
            return text
        at = _key_index(lines, span, old)
        if at is None:
            return text
        return joined([*lines[:at], render(table[old]), *lines[at + 1 :]])

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


def update_config(config_path: Path, backups_dir: Path, changes: list[Migration]) -> bool:
    """One committed edit of config.toml — the launch table rides this.
    A missing file is bootstrap's business, and OSError is swallowed: an
    edit is never worth a crash. Returns whether the file changed."""
    return update_settings_file(config_path, backups_dir, "config", changes)


def update_providers(providers_path: Path, backups_dir: Path, changes: list[Migration]) -> bool:
    """Same machinery over providers.toml — the provider moves and the
    model picker's field saves ride this. Returns whether the file
    changed; False also covers an edit that could not land."""
    return update_settings_file(providers_path, backups_dir, "providers", changes)


def update_settings_file(
    path: Path, backups_dir: Path, stem: str, changes: list[Migration]
) -> bool:
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    # A file 0.3.0 wrote on native Windows carries the locale codepage
    # (`formatting.decode_text`); it counts as changed even when no
    # shape move applies, so this commit re-encodes it as UTF-8 for
    # good — backup first, like any other edit. Legacy shows in the
    # round-trip: valid UTF-8 re-encodes byte-exact, a fallback never.
    text = decode_text(raw)
    migrated = apply_migrations(text, changes)
    if migrated == text and text.encode("utf-8") == raw:
        return False
    return commit(path, backup_path(backups_dir, stem), text, migrated)


# ---------- the write machinery ----------


def backup_path(backups_dir: Path, stem: str) -> Path:
    """The next free dated backup name: `stem-YYYYMMDD.toml` for the
    day's first edit, `-N` appended for every further one — no edit ever
    overwrites an earlier state."""
    stamp = datetime.now().astimezone().strftime("%Y%m%d")
    path = backups_dir / f"{stem}-{stamp}.toml"
    n = 0
    while path.exists():
        n += 1
        path = backups_dir / f"{stem}-{stamp}-{n}.toml"
    return path


def commit(file: Path, backup: Path, text: str, migrated: str) -> bool:
    """The write behind every config edit: the pre-edit text kept under
    its own dated backup name — born 0600 in a 0700 backups dir — then
    the atomic replace. OSError is swallowed — an edit is never worth a
    crash — and False reports it.

    What is kept is `redacted`: a secret this edit replaced is redacted in
    the backup, so sealing a key or hashing a password does not leave the
    plain value behind in a file nobody deletes."""
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(backup.parent, 0o700)
        # Born 0600: never a moment (or a crash residue) at umask perms.
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(redacted(text, migrated))
        write_atomic(file, migrated)
    except OSError:
        return False
    return True


def redacted(text: str, migrated: str) -> str:
    """`text` as a backup may keep it: every secret — an `api_key`, a
    `password` — whose value the edit to `migrated` CHANGED, set to
    "[REDACTED]".

    Changed is the whole test, and it is enough. A secret is only ever
    rewritten because it was sealed, hashed, replaced or moved away, and
    what replaced it is in the live file; one the edit left alone is
    kept as it was, so restoring the backup still restores it. What a
    restore of a redacted one costs is typing it again.

    The redacted line loses its trailing comment, as any `set_key` line
    does. A text that does not parse is kept as it is — nothing in it
    can be found to redact."""
    before, after = parse(text), parse(migrated)
    if before is None or after is None:
        return text
    for section, table in _tables(before):
        now = _table(after, section)
        for key in _SECRETS:
            was = table.get(key)
            if not isinstance(was, str) or not was:
                continue
            if not isinstance(now, dict) or now.get(key) != was:
                text = set_key(section, key, f'{key} = "[REDACTED]"')(text)
    return text


def _tables(parsed: dict[str, object], prefix: str = "") -> list[tuple[str, dict[str, object]]]:
    """Every table in a parsed file under its dotted name, nested ones
    included — an old config's `[providers.name]` sections are tables
    inside a table, and their keys are secrets all the same."""
    found: list[tuple[str, dict[str, object]]] = []
    for name, value in parsed.items():
        if isinstance(value, dict):
            dotted = f"{prefix}{name}"
            found.append((dotted, value))
            found.extend(_tables(value, f"{dotted}."))
    return found


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


def _inserted_at_span_start(lines: list[str], span: tuple[int, int], new: list[str]) -> list[str]:
    """`new` placed directly under the section's header."""
    header, _ = span
    return lines[: header + 1] + new + lines[header + 1 :]


def _inserted_at_span_end(lines: list[str], span: tuple[int, int], new: list[str]) -> list[str]:
    """`new` placed after the section's last non-blank line."""
    header, end = span
    last = end - 1
    while last > header and not lines[last].strip():
        last -= 1
    return lines[: last + 1] + new + lines[last + 1 :]
