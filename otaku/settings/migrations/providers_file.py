"""The provider moves over providers.toml: any provider section still in
config.toml moved over, plain api keys sealed wherever they came from,
and the local providers' sections ensured — each step convergent, run at
every launch."""

import re
from collections.abc import Callable
from pathlib import Path

from otaku.formatting import toml_key, toml_scalar
from otaku.settings import read_settings, write_atomic
from otaku.settings.migrations.surgery import (
    Migration,
    attached_start,
    backup_path,
    commit,
    ensure_section,
    joined,
    parse,
    set_key,
    update_providers,
)
from otaku.settings.providers import ProviderConfig

_HEADER = re.compile(r"(\s*\[\s*)providers\s*\.\s*(.*)$")
_ANY_HEADER = re.compile(r"\s*\[")


def move_providers(config_path: Path, providers_path: Path, backups_dir: Path) -> None:
    """The cross-file move: every [providers.*] section still in
    config.toml leaves for providers.toml — appended to it, or founding
    it. A section providers.toml already has is simply dropped from the
    config: the new home wins. Runs at every launch and converges — a
    config with no provider sections is untouched, and a crash between
    the two writes (the new file first, so nothing is ever lost) heals
    on the next run. The pre-move config waits as a dated backup."""
    try:
        text = read_settings(config_path)
    except OSError:
        return
    try:
        existing = read_settings(providers_path)
    except OSError:
        existing = ""
    taken = parse(existing) or {}
    result = move_providers_text(text, set(taken))
    if result is None:
        return
    remaining, moved = result
    if moved:
        grown = existing.rstrip("\n") + "\n\n" + moved if existing.strip() else moved
        if parse(grown) is None:
            return
        try:
            write_atomic(providers_path, grown)
        except OSError:
            return
    commit(config_path, backup_path(backups_dir, "config"), text, remaining)


def move_providers_text(text: str, taken: set[str]) -> tuple[str, str] | None:
    """The pure half of `move_providers`: config.toml's [providers.*]
    sections extracted — each block as it is, comment lines attached
    above included, its header unprefixed — while the rest of the config
    survives untouched. A section whose name is in `taken` (providers.toml
    already has it) is removed but not extracted: the new home wins.
    Returns (what remains of the config, the extracted blocks — possibly
    empty), or None when the config has no provider sections, or when
    what remains would not parse: broken output must never reach a
    file."""
    parsed = parse(text)
    families = parsed.get("providers") if parsed else None
    if not isinstance(families, dict) or not families:
        return None

    lines = text.splitlines()
    spans: list[tuple[int, int, str, str]] = []
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if m:
            end = i + 1
            while end < len(lines) and not _ANY_HEADER.match(lines[end]):
                end += 1
            name = m.group(2).rstrip().removesuffix("]").strip().strip('"')
            spans.append((i, end, m.group(1) + m.group(2), name))
    if not spans:
        return None
    blocks: list[str] = []
    for i, end, new_header, name in reversed(spans):
        start = attached_start(lines, (-1, end), i)
        block = lines[start:end]
        block[i - start] = new_header
        while block and not block[-1].strip():
            block.pop()
        if name not in taken:
            blocks.append("\n".join(block))
        del lines[start:end]
    blocks.reverse()
    while lines and not lines[0].strip():
        del lines[0]
    remaining = joined(lines) if lines else ""
    moved = "\n\n".join(blocks) + "\n" if blocks else ""
    if parse(remaining) is None or (moved and parse(moved) is None):
        return None
    return remaining, moved


def seal_api_keys(seal: Callable[[str], str], is_sealed: Callable[[str], bool]) -> Migration:
    """A migration sealing every plain, non-empty api_key in the file —
    however it got there: moved from an old config, typed in by hand, or
    left plain by an earlier launch that could not seal. The migration
    decides its own applicability (`is_sealed` is injected with the
    sealer — this package may not reach the encryption plane, and this
    module touches no key material of its own): a sealed or empty key is
    never handed to `seal`, and a seal that returns its input unchanged
    (impossible right now) leaves the line for the next launch."""

    def apply(text: str) -> str:
        parsed = parse(text)
        if parsed is None:
            return text
        for name, table in parsed.items():
            if not isinstance(table, dict):
                continue
            key = table.get("api_key")
            if isinstance(key, str) and key and not is_sealed(key):
                value = seal(key)
                if value != key:
                    text = set_key(name, "api_key", f"api_key = {toml_scalar(value)}")(text)
        return text

    return apply


def ensure_providers(
    providers_path: Path, backups_dir: Path, defaults: dict[str, ProviderConfig]
) -> None:
    """The launch step over providers.toml: every given backend keeps a
    section — what first run writes, ensured thereafter, so a provider
    the app learned after this install still shows up in the picker.
    Present sections are never touched; like every migration this
    converges, so a deleted section returns — retire a provider by
    leaving its section pointing nowhere instead."""
    changes = []
    for config in defaults.values():
        head = f"[{toml_key(config.name)}]\nurl = {toml_scalar(config.url)}\n"
        block = head + 'api_key = ""'
        if config.keep_alive:
            block += f"\nkeep_alive = {toml_scalar(config.keep_alive)}"
        changes.append(ensure_section(config.name, block))
    update_providers(providers_path, backups_dir, changes)
