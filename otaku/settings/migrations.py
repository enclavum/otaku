"""Config migrations: the one way the app edits the settings files.

config.toml and providers.toml are edited surgically — line by line, the
exact rows a change requires, never a re-render: every other byte,
comment, and blank of the file survives. `migrate` is the whole launch
step: the shape-change table over config.toml, then the one cross-file
migration splitting an old config's provider sections out into
providers.toml, then the known backends' sections ensured there.
`update_providers` is the same machinery serving the app's few explicit
edits (the model picker's provider fields). `_migrations` is the ordered
table, one entry per shape change across app versions, built from the
factories below. Every entry is idempotent and the table convergent, so
it simply runs at every launch — no version stamp to trust, or to lose
when a config file travels between machines — and a file is written only
when something actually changed, its pre-edit text kept as a dated
backup in configs/backups/.

Applicability is decided on the PARSED file (a key mentioned in a comment
or a string can never false-match; a commented-out `# key = …` counts as
absent) while the edit itself is textual. The chained result must parse
back or it is discarded wholesale — a migration bug leaves the file old,
never broken — and the write is atomic.

The textual scan is line-based, which is sound for these files because
they hold no multiline strings by construction; prompts.toml (multiline
templates) must not be migrated with these tools.
"""

import re
import tomllib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from otaku.paths import Paths
from otaku.settings import sealed
from otaku.settings.config import Provider
from otaku.settings.files import row, toml_key, toml_scalar, write_atomic

# One shape change: config text in, config text out (unchanged when the
# change does not apply).
Migration = Callable[[str], str]

_ANY_HEADER = re.compile(r"\s*\[")


def _migrations() -> list[Migration]:
    """The shape-change table, oldest first: one factory call per change
    across app versions, each safe to re-run on any config the app ever
    wrote. A function, not a constant, only so the table can sit here on
    top — its entries call the factories defined below."""
    return [
        # 0.2.2 — dialogue coloring arrives with the [ui] section.
        ensure_section(
            "ui",
            "[ui]\n"
            + row(
                'dialogue_color = "auto"',
                'spoken lines: "auto" fits the background; a color name ("cyan") or #rrggbb',
            )
            + "\n"
            + row("dialogue_bold = false", "also bold the spoken lines"),
            after="settings",
        ),
    ]


def ensure_section(name: str, block: str, after: str = "") -> Migration:
    """A migration adding a whole new `[name]` section: when the parsed
    file has no such table, `block` — the header line and its rows,
    exactly as a fresh config would render them — is inserted right below
    the `[after]` section, one separating blank line between, so the
    migrated file keeps the fresh file's order. Without `after`, or when
    the file no longer has that section, the block is appended at the end
    instead. Present, even empty: untouched."""

    def apply(text: str) -> str:
        parsed = _parse(text)
        if parsed is None or name in parsed:
            return text
        chunk = block if block.endswith("\n") else block + "\n"
        lines = text.splitlines()
        span = _section_span(lines, after) if after else None
        if span is None:
            return text.rstrip("\n") + "\n\n" + chunk
        return _joined(_inserted_at_span_end(lines, span, ["", *chunk.splitlines()]))

    return apply


def set_key(section: str, key: str, line: str) -> Migration:
    """A migration replacing the `key = …` line of `[section]` (dotted
    names reach child tables) with `line`, the freshly rendered
    `key = value` row: the value is the change, so a trailing comment on
    the old line goes with it, while comment lines above stay. An absent
    key is added at the section's end; no `[section]` in the file means
    no edit. Unlike `ensure_section`, which never touches what exists,
    this imposes the value — the app's explicit edits, through
    `update_providers`."""

    def apply(text: str) -> str:
        parsed = _parse(text)
        if parsed is None or not isinstance(_child(parsed, section), dict):
            return text
        lines = text.splitlines()
        span = _section_span(lines, section)
        if span is None:
            return text
        at = _key_index(lines, span, key)
        if at is None:
            return _joined(_inserted_at_span_end(lines, span, [line]))
        if lines[at] == line:
            return text
        return _joined([*lines[:at], line, *lines[at + 1 :]])

    return apply


def drop_child_key(parent: str, key: str) -> Migration:
    """A migration removing a retired `key = value` line from every
    `[parent.<name>]` child section, the names being the user's own.
    Comment lines attached directly above each removed line go with it.
    Absent: untouched."""

    def apply(text: str) -> str:
        parsed = _parse(text)
        if parsed is None:
            return text
        children = parsed.get(parent)
        if not isinstance(children, dict):
            return text
        for name, table in children.items():
            if isinstance(table, dict) and key in table:
                text = _dropped(text, f"{parent}.{name}", key)
        return text

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


def migrate(paths: Paths, providers: dict[str, Provider]) -> None:
    """The whole launch step over the settings files, in order: the table
    over config.toml, the provider split, the given backends' sections
    ensured in providers.toml. A missing config is bootstrap's business,
    and any OSError is swallowed — a migration is never worth a launch."""
    try:
        text = paths.config_file.read_text()
    except OSError:
        return
    migrated = apply_migrations(text, _migrations())
    if migrated != text:
        _commit(paths.config_file, _backup_path(paths, "config"), text, migrated)
    _split_providers(paths)
    _ensure_providers(paths, providers)


def update_providers(paths: Paths, changes: list[Migration]) -> bool:
    """One explicit edit of providers.toml, now — a value the user just
    typed rides the exact machinery of `migrate`: the same parse-guided
    surgery, the same discard of a result that no longer parses, the
    same backup and atomic write. Returns whether the file changed."""
    try:
        text = paths.providers_file.read_text()
    except OSError:
        return False
    migrated = apply_migrations(text, changes)
    if migrated == text:
        return False
    return _commit(paths.providers_file, _backup_path(paths, "providers"), text, migrated)


def split_providers_text(text: str, seal: Callable[[str], str]) -> tuple[str, str] | None:
    """The pure half of the provider split: an old config.toml's
    [providers.*] sections become the body of providers.toml — each
    block moved as it is, comment lines attached above included, its
    header unprefixed — while the rest of the config survives untouched.
    On the way the retired supports_thinking key is dropped and a plain,
    non-empty api key passes through `seal`. Returns (what remains of
    the config, the new file's text), or None when the config has no
    provider sections — or when either result would not parse: broken
    output must never reach a file."""
    parsed = _parse(text)
    families = parsed.get("providers") if parsed else None
    if not isinstance(families, dict) or not families:
        return None
    work = drop_child_key("providers", "supports_thinking")(text)
    for name, table in families.items():
        if not isinstance(table, dict):
            continue
        key = table.get("api_key")
        if isinstance(key, str) and key and not sealed.is_sealed(key):
            line = f"api_key = {toml_scalar(seal(key))}"
            work = set_key(f"providers.{name}", "api_key", line)(work)

    lines = work.splitlines()
    header = re.compile(r"(\s*\[\s*)providers\s*\.\s*(.*)$")
    spans: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = header.match(line)
        if m:
            end = i + 1
            while end < len(lines) and not _ANY_HEADER.match(lines[end]):
                end += 1
            spans.append((i, end, m.group(1) + m.group(2)))
    if not spans:
        return None
    blocks: list[str] = []
    for i, end, new_header in reversed(spans):
        start = _attached_start(lines, (-1, end), i)
        block = lines[start:end]
        block[i - start] = new_header
        while block and not block[-1].strip():
            block.pop()
        blocks.append("\n".join(block))
        del lines[start:end]
    blocks.reverse()
    while lines and not lines[0].strip():
        del lines[0]
    remaining = _joined(lines) if lines else ""
    moved = "\n\n".join(blocks) + "\n"
    if _parse(remaining) is None or _parse(moved) is None:
        return None
    return remaining, moved


def _split_providers(paths: Paths) -> None:
    """The one cross-file migration: configs/providers.toml is born from
    an old config.toml's [providers.*] sections. It runs only while
    providers.toml does not exist AND the main config still has provider
    sections — after the move the sections are gone, so it never fires
    again. Plain api keys are sealed on the way; a key that cannot be
    sealed moves as it is — a migration is never worth a launch. The
    pre-move config.toml is kept as a dated backup, and the new file is
    written first, so a crash between the two writes loses nothing."""
    if paths.providers_file.exists():
        return
    try:
        text = paths.config_file.read_text()
    except OSError:
        return

    def sealer(value: str) -> str:
        try:
            return sealed.seal(paths, value)
        except sealed.SealedError:
            return value

    result = split_providers_text(text, sealer)
    if result is None:
        return
    remaining, moved = result
    try:
        write_atomic(paths.providers_file, moved)
    except OSError:
        return
    _commit(paths.config_file, _backup_path(paths, "config"), text, remaining)


def _ensure_providers(paths: Paths, providers: dict[str, Provider]) -> None:
    """The launch step over providers.toml: every given backend keeps a
    section — what first run writes, ensured thereafter, so an engine
    the app learned after this install still shows up in the picker.
    Present sections are never touched; like every migration this
    converges, so a deleted section returns — retire an engine by
    leaving its section pointing nowhere instead."""
    changes = []
    for provider in providers.values():
        block = f"[{toml_key(provider.name)}]\nurl = {toml_scalar(provider.url)}\n" + 'api_key = ""'
        if provider.keep_alive:
            block += f"\nkeep_alive = {toml_scalar(provider.keep_alive)}"
        changes.append(ensure_section(provider.name, block))
    update_providers(paths, changes)


def _backup_path(paths: Paths, stem: str) -> Path:
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


def _commit(file: Path, backup: Path, text: str, migrated: str) -> bool:
    """The write behind every config edit: the pre-edit text kept under
    its own dated backup name, then the atomic replace. OSError is
    swallowed — an edit is never worth a crash — and False reports it."""
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(text)
        write_atomic(file, migrated)
    except OSError:
        return False
    return True


# ---------- the textual scan ----------


def _parse(text: str) -> dict[str, object] | None:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


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
    start = _attached_start(lines, span, at)
    return _joined(lines[:start] + lines[at + 1 :])


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
