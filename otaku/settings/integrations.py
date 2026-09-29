"""The integration sections: integrations.toml — the [NAME] tables and
their type, and the one CHOICE the file holds besides them: which search
engine web search asks (`[web_search] engine`, a section's name).
`IntegrationConfig` lives HERE (integration settings are settings); the
integrations package imports it, and nothing converts in between."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from otaku.formatting import toml_key, toml_scalar
from otaku.settings import read_settings
from otaku.settings.config import ConfigError

# The section that names a service rather than being one: the engine
# web search asks, under its one key — "tavily", "exa", "" for none.
WEB_SEARCH_SECTION = "web_search"
ENGINE_KEY = "engine"


@dataclass(frozen=True)
class IntegrationConfig:
    """One integration as configured: a service otaku asks for something
    other than a completion, at its url, with its key."""

    name: str
    url: str
    api_key: str = ""


@dataclass(frozen=True)
class IntegrationSettings:
    """The integration settings as read: each integration's config by
    name, and the engine chosen for web search — an integration's name,
    "" while none is chosen."""

    integrations: dict[str, IntegrationConfig] = field(default_factory=dict)
    web_search_engine: str = ""


def load(path: Path) -> IntegrationSettings:
    """The [NAME] sections, validated (a section must carry a url), and
    the web search choice; an empty file holds none. Raises ConfigError
    — the file is hand-edited, so errors must be human."""
    try:
        raw = tomllib.loads(read_settings(path))
    except FileNotFoundError as e:
        raise ConfigError(f"{path} does not exist") from e
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path}: not valid UTF-8 — save the file as UTF-8") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML — {e}") from e
    sections = {name: entry for name, entry in raw.items() if isinstance(entry, dict)}
    choice = sections.pop(WEB_SEARCH_SECTION, {}).get(ENGINE_KEY, "")
    if not isinstance(choice, str):
        raise ConfigError(f"{path}: [{WEB_SEARCH_SECTION}] {ENGINE_KEY} must be a string")
    configs: dict[str, IntegrationConfig] = {}
    for name, entry in sections.items():
        if "url" not in entry:
            raise ConfigError(f"{path}: [{name}] must have a 'url' key")
        configs[name] = IntegrationConfig(
            name=str(name), url=str(entry["url"]).rstrip("/"), api_key=str(entry.get("api_key", ""))
        )
    return IntegrationSettings(configs, choice)


def render(configs: dict[str, IntegrationConfig], web_search_engine: str = "") -> str:
    """integrations.toml text — one top-level [name] section per
    integration, then the web search choice; what first run writes."""
    lines = [
        "# otaku integrations — one [name] section per service otaku asks for",
        "# something other than a completion. Api keys are stored sealed.",
    ]
    for config in configs.values():
        lines += ["", section_block(config)]
    lines += ["", web_search_block(web_search_engine)]
    return "\n".join(lines) + "\n"


def section_block(config: IntegrationConfig) -> str:
    """One integration's section as the file renders it — the rows a
    launch founds it with, and a key save founds it with where a hand
    edit dropped it (`surgery.ensure_section`)."""
    return (
        f"[{toml_key(config.name)}]\n"
        f"url = {toml_scalar(config.url)}\n"
        f"api_key = {toml_scalar(config.api_key)}"
    )


def web_search_block(engine: str) -> str:
    """The choice's section as the file renders it: which engine's
    section web search asks, set by the key saved in either frontend."""
    return f"[{WEB_SEARCH_SECTION}]\n{ENGINE_KEY} = {toml_scalar(engine)}"
