"""Filesystem layout of the state dir — application knowledge, owned by
the composition root. Everything below backend takes plain Path values
derived here; nothing else knows the tree.

Layout: configs/ holds what the user edits and what the app remembers,
database/ the story store and its backups, logs/ the append-only logs.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Self

DEFAULT_ROOT = "~/.otaku"


@dataclass(frozen=True)
class Paths:
    root: Path

    @classmethod
    def resolve(cls, root: str | Path | None = None) -> Self:
        """The state dir: `root` when given, else the default. The env
        var belongs to cli, which passes the resolved root in."""
        return cls(root=Path(root or DEFAULT_ROOT).expanduser())

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
        """User-owned configuration; the app writes it once at first run
        and thereafter touches it only through settings migrations."""
        return self.configs_dir / "config.toml"

    @property
    def providers_file(self) -> Path:
        """User-owned provider sections — one top-level [name] table per
        provider; the model picker edits it surgically."""
        return self.configs_dir / "providers.toml"

    @property
    def config_backups_dir(self) -> Path:
        """Pre-migration snapshots of the config files, dated like the
        database backups."""
        return self.configs_dir / "backups"

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

    @property
    def config_key_file(self) -> Path:
        """The sealing key for api keys, when it lives on disk rather
        than in the OS keychain (see `encryption.keys`)."""
        return self.configs_dir / "config.key"

    # web/ — the reader's own front-end files

    @property
    def custom_web_dir(self) -> Path:
        """User-owned: what the web frontend loads AFTER its own styles,
        `custom.css` alone today. Never created by the app — an absent
        directory is the normal state, and the frontend answers with an
        empty stylesheet."""
        return self.root / "web"

    # cert/ — what `otaku web` serves under when https is on

    @property
    def cert_dir(self) -> Path:
        """The TLS pair the web frontend serves under. Created on the
        first launch that needs it rather than with the rest of the tree,
        and never written over once it holds a pair — so a certificate
        the reader drops in here is the one that is served."""
        return self.root / "cert"

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

    @property
    def keychain_service(self) -> str:
        """The OS-keychain service label, named per state dir so parallel
        setups never share a key."""
        return f"otaku:{self.root}"
