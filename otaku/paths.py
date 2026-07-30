"""Filesystem layout of the state dir.

The state dir is resolved once at startup — from OTAKU_CONFIG_DIR, falling
back to ~/.otaku — into an immutable `Paths` value that is passed
explicitly to everything that touches disk. Nothing derives a path at import
time.

Layout: configs/ holds what the user edits and what the app remembers,
database/ the story store and its backups, logs/ the append-only logs.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self

_ENV_VAR = "OTAKU_CONFIG_DIR"
_DEFAULT_ROOT = "~/.otaku"


@dataclass(frozen=True)
class Paths:
    root: Path

    @classmethod
    def resolve(cls, root: str | Path | None = None) -> Self:
        """The state dir: `root` when given, else $OTAKU_CONFIG_DIR, else
        the default."""
        raw = root or os.environ.get(_ENV_VAR, "").strip() or _DEFAULT_ROOT
        return cls(root=Path(raw).expanduser())

    def ensure_tree(self) -> None:
        """Create the state dir layout."""
        for directory in (self.configs_dir, self.database_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # configs/ — the user's files and the app's remembered state

    @property
    def configs_dir(self) -> Path:
        return self.root / "configs"

    @property
    def config_file(self) -> Path:
        """User-owned configuration; the app writes it once at first run."""
        return self.configs_dir / "config.toml"

    @property
    def state_file(self) -> Path:
        """App-owned: what otaku remembers between sessions."""
        return self.configs_dir / "state.toml"

    @property
    def models_file(self) -> Path:
        """App-owned: per-model overrides written by /set."""
        return self.configs_dir / "models.toml"

    @property
    def prompts_file(self) -> Path:
        """User-editable prompt templates."""
        return self.configs_dir / "prompts.toml"

    @property
    def keys_file(self) -> Path:
        """The keystore: the wrapped data-encryption key and its KEK slots."""
        return self.configs_dir / "keys.toml"

    @property
    def kek_file(self) -> Path:
        """The key-encryption key of the `disk` provider."""
        return self.configs_dir / "kek.key"

    # database/ — the story store

    @property
    def database_dir(self) -> Path:
        return self.root / "database"

    @property
    def database_file(self) -> Path:
        return self.database_dir / "history.db"

    @property
    def backups_dir(self) -> Path:
        return self.database_dir / "backups"

    # logs/ — append-only

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"
