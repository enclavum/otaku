"""Config migrations: the one way the app edits the settings files.

This module holds the shape-change tables themselves and `migrate`, the
whole launch step; `surgery` is the toolkit every edit is built from,
and `providers` the moves over providers.toml. Each file has its own
ordered table — `_config_migrations` and `_provider_migrations` — one
entry per shape change across app versions. Everything here is
idempotent and convergent: it all simply reruns at every launch — no
version stamp to trust, no one-shot step whose half-state could stick —
so a crash between writes, a hand edit, or a launch that could not
finish (a key that would not seal, say) heals on the next one. A file
is written only when something actually changed.
"""

import contextlib
from collections.abc import Callable

from otaku.paths import Paths
from otaku.settings.config import ProviderConfig
from otaku.settings.files import row, write_atomic
from otaku.settings.migrations.providers import (
    ensure_providers,
    move_providers,
    seal_api_keys,
    sealer,
)
from otaku.settings.migrations.surgery import (
    Migration,
    apply_migrations,
    drop_key_everywhere,
    ensure_section,
    set_key,
    update_config,
    update_providers,
)

__all__ = [
    "Migration",
    "apply_migrations",
    "ensure_section",
    "migrate",
    "set_key",
    "update_providers",
]


def _config_migrations() -> list[Migration]:
    """config.toml's shape-change table, oldest first: one factory call
    per change across app versions, each safe to re-run on any config
    the app ever wrote. A function, not a constant, only so the table
    can sit here on top — its entries call the `surgery` factories."""
    return [
        # 0.2.2 — dialogue coloring arrives with the [ui] section.
        ensure_section(
            "ui",
            "[ui]\n"
            + row(
                'dialogue_color = "auto"',
                'spoken lines: "auto" is the shipped dark blue; a color name ("cyan") or #rrggbb',
            )
            + "\n"
            + row("dialogue_bold = false", "also bold the spoken lines"),
            after="settings",
        ),
    ]


def _provider_migrations(seal: Callable[[str], str]) -> list[Migration]:
    """providers.toml's shape-change table. Its sections carry the
    user's own names, so an entry here sweeps all of them — and runs
    after the move from an old config, so it cleans a section the same
    way wherever the section came from."""
    return [
        # 0.2.2 — thinking support became class knowledge of the backend.
        drop_key_everywhere("supports_thinking"),
        # 0.2.2 — api keys live sealed; a plain one (hand-typed, or left
        # by a launch that could not seal) is sealed as soon as possible.
        seal_api_keys(seal),
    ]


def migrate(paths: Paths, providers: dict[str, ProviderConfig]) -> None:
    """The whole launch step over the settings files, in order: the
    config table, the provider move, the providers table, the given
    backends' sections ensured. providers.toml itself converges too:
    missing beside an existing config — a crash between the first-run
    writes, a hand deletion — it is founded empty here, for the ensured
    sections to fill. A missing config is bootstrap's business, and
    failures are swallowed — a migration is never worth a launch."""
    update_config(paths, _config_migrations())
    move_providers(paths)
    if paths.config_file.exists() and not paths.providers_file.exists():
        with contextlib.suppress(OSError):
            write_atomic(paths.providers_file, "")
    update_providers(paths, _provider_migrations(sealer(paths)))
    ensure_providers(paths, providers)
