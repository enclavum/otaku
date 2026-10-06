"""The integration sections: integrations.toml — the [NAME] tables and
their type. `IntegrationConfig` lives HERE (integration settings are
settings); the integrations package imports it, and nothing converts in
between."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from otaku.formatting import toml_key, toml_scalar
from otaku.settings import read_settings
from otaku.settings.config import ConfigError


@dataclass(frozen=True)
class IntegrationConfig:
    """One integration as configured: a service otaku asks for something
    other than a completion, at its url ("" where its class fixes it),
    with its key."""

    name: str
    url: str
    api_key: str = ""


@dataclass(frozen=True)
class IntegrationSettings:
    """The integration settings as read: each integration's config by
    name."""

    integrations: dict[str, IntegrationConfig] = field(default_factory=dict)


def load(path: Path) -> IntegrationSettings:
    """The [NAME] sections; an empty file holds none. Raises ConfigError
    — the file is hand-edited, so errors must be human."""
    try:
        raw = tomllib.loads(read_settings(path))
    except FileNotFoundError as e:
        raise ConfigError(f"{path} does not exist") from e
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path}: not valid UTF-8 — save the file as UTF-8") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML — {e}") from e
    configs = {
        name: IntegrationConfig(
            name=str(name),
            url=str(entry.get("url", "")).rstrip("/"),
            api_key=str(entry.get("api_key", "")),
        )
        for name, entry in raw.items()
        if isinstance(entry, dict)
    }
    return IntegrationSettings(configs)


def render(configs: dict[str, IntegrationConfig]) -> str:
    """integrations.toml text — one top-level [name] section per
    integration; what first run writes."""
    lines = [
        "# otaku integrations — one [name] section per service otaku asks for",
        "# something other than a completion. Api keys are stored sealed.",
    ]
    for config in configs.values():
        lines += ["", section_block(config)]
    return "\n".join(lines) + "\n"


def section_block(config: IntegrationConfig) -> str:
    """One integration's section as the file renders it — the rows a
    launch founds it with, and a key save founds it with where a hand
    edit dropped it (`surgery.ensure_section`); a url row only where
    there is one."""
    url = f"url = {toml_scalar(config.url)}\n" if config.url else ""
    return f"[{toml_key(config.name)}]\n{url}api_key = {toml_scalar(config.api_key)}"
