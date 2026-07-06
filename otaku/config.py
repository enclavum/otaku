"""Config loader for ~/.otaku/config.toml.

Bootstraps a default config on first run, assembled from each component's own
default: the database section from the storage layer and one section per
built-in provider client (see `default_config`). The config schema is open:
extra [providers.NAME] sections are picked up automatically.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Undocumented escape hatch: OTAKU_CONFIG_DIR points otaku at an alternate
# config/state directory (config.toml, keys.json, model_defaults.json,
# last_model); empty/unset falls back to ~/.otaku. crypto.py derives its key
# paths from CONFIG_DIR at import, so this relocates the whole state dir at once
# — handy for throwaway or parallel setups without touching $HOME.
CONFIG_DIR_ENV = "OTAKU_CONFIG_DIR"


def _default_config_dir() -> Path:
    env = os.environ.get(CONFIG_DIR_ENV)
    return Path(env).expanduser() if env else Path.home() / ".otaku"


CONFIG_DIR = _default_config_dir()
CONFIG_PATH = CONFIG_DIR / "config.toml"
LAST_MODEL_PATH = CONFIG_DIR / "last_model"
# otaku-managed per-model defaults written by /remember (keyed by bare model
# name). Kept out of config.toml so it can be rewritten safely without a
# TOML writer or clobbering the user's hand-edited config.
MODEL_DEFAULTS_PATH = CONFIG_DIR / "model_defaults.json"

# Env var that wins over [database].url in config.toml. Useful for
# scripted runs against a different DB without rewriting the config.
DATABASE_URL_ENV = "OTAKU_DATABASE_URL"

# First-run [defaults] stub (all commented) — makes the section discoverable
# without changing behaviour. Owned here so default_config() can assemble it.
DEFAULTS_CONFIG = """\
[defaults]
# system = "You are concise."
# think = "none"           # none | low | medium | high | max | default
# no_record = false        # open every session in --no-record (read-only) mode
# create_summaries = true  # generate conversation summaries in the background
# summary_idle_seconds = 5 # summarize after this many seconds idle at the prompt
# [defaults.parameters]
# temperature = 0.7
"""


def read_last_model() -> str | None:
    if not LAST_MODEL_PATH.exists():
        return None
    spec = LAST_MODEL_PATH.read_text().strip()
    return spec or None


def write_last_model(spec: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LAST_MODEL_PATH.write_text(spec + "\n")


def default_config() -> str:
    """Assemble the first-run config.toml: the database + encryption + defaults
    sections, plus each provider client's `default_config_section()` — a
    `[providers.NAME]` section for every built-in local engine with its port
    (and api key) auto-detected from the machine at this one write. The sections
    are authoritative thereafter (never re-detected). Imports are local because
    both `client` and `storage` import this module."""
    from otaku.client import PROBE_CHAIN
    from otaku.storage import crypto
    from otaku.storage.store import Store

    sections = [Store.DEFAULT_CONFIG, crypto.DEFAULT_CONFIG, DEFAULTS_CONFIG]
    sections += [s for cls in PROBE_CHAIN if (s := cls.default_config_section()) is not None]
    return "\n\n".join(s.strip() for s in sections) + "\n"


@dataclass(frozen=True)
class Provider:
    name: str
    url: str
    api_key: str
    supports_thinking: bool
    keep_alive: str = "24h"  # only used by ollama's explicit load action
    # only used by omlx: de-jitter its bursty output stream
    smoothen_streaming: bool = False


@dataclass(frozen=True)
class Encryption:
    provider: str = "keychain"  # keychain | passphrase | disk | command
    retrieve_command: str | None = None  # used only by provider = "command"


@dataclass(frozen=True)
class Settings:
    """Persisted session defaults for one scope (global `[defaults]` or a
    per-model override). `None` means 'not specified' — inherit from the lower
    layer; a present value overrides. `think` uses the same vocabulary as
    `/set think` (`none`/`low`/…/`max`, or `default` = send no reasoning_effort).
    """

    system: str | None = None
    think: str | None = None
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    database_url: str
    providers: dict[str, Provider]
    encryption: Encryption
    defaults: Settings = field(default_factory=Settings)
    model_defaults: dict[str, Settings] = field(default_factory=dict)
    no_record: bool = False  # [defaults].no_record — open every session read-only
    # [defaults].create_summaries — generate conversation summaries in a
    # background worker (idle-debounced). summary_idle_seconds is the pause at
    # the prompt after which the current conversation is summarized.
    create_summaries: bool = True
    summary_idle_seconds: float = 5.0


def load(path: Path = CONFIG_PATH) -> Config:
    """Load config from disk, writing the default file if it doesn't exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(default_config())

    raw = tomllib.loads(path.read_text())

    from otaku.storage.store import Store  # local: avoid import cycle

    database_url = os.environ.get(DATABASE_URL_ENV) or raw.get("database", {}).get(
        "url", Store.DEFAULT_URL
    )

    providers_raw = raw.get("providers", {})
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise ValueError(f"{path}: at least one [providers.NAME] section is required")

    providers: dict[str, Provider] = {}
    for name, entry in providers_raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: [providers.{name}] must be a table")
        if "url" not in entry:
            raise ValueError(f"{path}: [providers.{name}] missing required key 'url'")
        providers[name] = Provider(
            name=name,
            url=str(entry["url"]).rstrip("/"),
            api_key=str(entry.get("api_key", "")),
            supports_thinking=bool(entry.get("supports_thinking", False)),
            keep_alive=str(entry.get("keep_alive", "24h")),
            smoothen_streaming=bool(entry.get("smoothen_streaming", False)),
        )

    enc_raw = raw.get("encryption", {})
    if not isinstance(enc_raw, dict):
        raise ValueError(f"{path}: [encryption] must be a table")
    encryption = Encryption(
        provider=str(enc_raw.get("provider", "keychain")),
        retrieve_command=enc_raw.get("retrieve_command"),
    )

    defaults_raw = raw.get("defaults", {})
    if not isinstance(defaults_raw, dict):
        raise ValueError(f"{path}: [defaults] must be a table")

    return Config(
        database_url=database_url,
        providers=providers,
        encryption=encryption,
        defaults=_parse_settings(defaults_raw),
        model_defaults=_read_model_defaults(),
        no_record=bool(defaults_raw.get("no_record", False)),
        create_summaries=bool(defaults_raw.get("create_summaries", True)),
        summary_idle_seconds=float(defaults_raw.get("summary_idle_seconds", 5.0)),
    )


# ---------- session defaults ([defaults] + per-model overrides) ----------


def _parse_settings(d: dict[str, object]) -> Settings:
    system = d.get("system")
    think = d.get("think")
    params = d.get("parameters", {})
    return Settings(
        system=str(system) if system not in (None, "") else None,
        think=str(think) if think is not None else None,
        parameters=dict(params) if isinstance(params, dict) else {},
    )


def _read_model_defaults() -> dict[str, Settings]:
    """Load per-model overrides from model_defaults.json. Best effort — a
    missing or malformed file just yields no overrides."""
    if not MODEL_DEFAULTS_PATH.exists():
        return {}
    try:
        raw = json.loads(MODEL_DEFAULTS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, Settings] = {}
    if isinstance(raw, dict):
        for name, entry in raw.items():
            if isinstance(entry, dict):
                out[str(name)] = _parse_settings(entry)
    return out


def settings_for(cfg: Config, model: str) -> Settings:
    """Effective session defaults for `model` (bare name): the per-model
    override layered over the global `[defaults]`."""
    g = cfg.defaults
    m = cfg.model_defaults.get(model)
    if m is None:
        return g
    return Settings(
        system=m.system if m.system is not None else g.system,
        think=m.think if m.think is not None else g.think,
        parameters={**g.parameters, **m.parameters},
    )


def remember_model_settings(model: str, settings: Settings) -> None:
    """Persist per-model defaults for `model` (bare name) to
    model_defaults.json, preserving other models' entries. An all-empty
    `settings` clears the model's entry."""
    data: dict[str, object] = {}
    if MODEL_DEFAULTS_PATH.exists():
        try:
            loaded = json.loads(MODEL_DEFAULTS_PATH.read_text())
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            data = {}
    entry: dict[str, object] = {}
    if settings.system is not None:
        entry["system"] = settings.system
    if settings.think is not None:
        entry["think"] = settings.think
    if settings.parameters:
        entry["parameters"] = settings.parameters
    if entry:
        data[model] = entry
    else:
        data.pop(model, None)
    MODEL_DEFAULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_DEFAULTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
