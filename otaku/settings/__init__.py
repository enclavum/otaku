"""The configuration files under configs/, each function handed the plain
Path it reads or writes — this package resolves no locations itself.

`config` owns config.toml (user-owned, edited only by migrations),
`providers` the providers.toml sections and their `ProviderConfig` type
(provider settings are settings), `state` what the app remembers
between sessions, `models` the per-model parameter overrides,
`prompts` the model-facing templates, and `migrations` the convergent
launch-time edits. The write helpers every sibling shares live right
here. Api-key sealing is NOT here: it is encryption's second plane
(`encryption.keys`), reached by the backend — this package touches no
key material.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from otaku.formatting import decode_text

# Written config lines align their comments to one column, so the values
# read as a column instead of a wall of prose.
_COMMENT_COLUMN = 30


@dataclass(frozen=True)
class SettingsFiles:
    """Where the files this package works on live — handed down by the
    launch, since this package resolves no locations itself: the four
    settings files, the per-model overrides, and the directory the
    surgical edits keep their backups in."""

    config: Path
    providers: Path
    prompts: Path
    state: Path
    models: Path
    backups_dir: Path


@dataclass(frozen=True)
class Secrets:
    """The encryption plane, injected by the launch: this package may
    not reach it, and only the migrations use it. `seal` seals a plain
    api key and `is_sealed` tells one already sealed, so the migration
    skips those itself; `hash` and `is_hashed` the same for the web
    password."""

    seal: Callable[[str], str]
    is_sealed: Callable[[str], bool]
    hash: Callable[[str], str]
    is_hashed: Callable[[str], bool]


def row(setting: str, comment: str) -> str:
    """One config line with its comment aligned to the shared column. A
    setting longer than the column still gets two spaces before the #."""
    if not comment:
        return setting
    if len(setting) < _COMMENT_COLUMN:
        return f"{setting:<{_COMMENT_COLUMN}}# {comment}"
    return f"{setting}  # {comment}"


def read_settings(path: Path) -> str:
    """A settings file as text, whatever encoding wrote it — see
    `formatting.decode_text`: a 0.3.0 on native Windows wrote the ANSI
    codepage, and an upgrade must read it rather than die. The next
    settings write re-encodes the file as UTF-8."""
    return decode_text(path.read_bytes())


def write_atomic(path: Path, text: str) -> None:
    """Write via tmp + rename, so a kill mid-write can never truncate.
    Newlines go out verbatim (`newline=""`): Windows' \n → \r\n
    translation would re-mangle a file the lenient read just
    normalized, and settings files are LF on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
    tmp.replace(path)
