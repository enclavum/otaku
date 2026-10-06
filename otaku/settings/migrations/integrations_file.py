"""The launch step over integrations.toml: every configurable
integration keeps a section — what first run writes, ensured thereafter,
so an integration the app learned after this install still shows where
its key goes. Present sections are never touched; like every migration
this converges, so a deleted section returns."""

from pathlib import Path

from otaku.settings.integrations import IntegrationConfig, section_block
from otaku.settings.migrations.surgery import ensure_section, update_settings_file


def ensure_integrations(
    path: Path, backups_dir: Path, defaults: dict[str, IntegrationConfig]
) -> None:
    changes = [ensure_section(config.name, section_block(config)) for config in defaults.values()]
    update_settings_file(path, backups_dir, changes)
