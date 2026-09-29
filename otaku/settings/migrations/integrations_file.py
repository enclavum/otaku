"""The launch step over integrations.toml: every known integration keeps
a section, and the web search choice its own — what first run writes,
ensured thereafter, so an integration the app learned after this
install still shows where its key goes. Present sections are never
touched; like every migration this converges, so a deleted section
returns."""

from pathlib import Path

from otaku.settings.integrations import (
    WEB_SEARCH_SECTION,
    IntegrationConfig,
    section_block,
    web_search_block,
)
from otaku.settings.migrations.surgery import ensure_section, update_settings_file


def ensure_integrations(
    path: Path, backups_dir: Path, defaults: dict[str, IntegrationConfig]
) -> None:
    changes = [ensure_section(config.name, section_block(config)) for config in defaults.values()]
    changes.append(ensure_section(WEB_SEARCH_SECTION, web_search_block("")))
    update_settings_file(path, backups_dir, changes)
