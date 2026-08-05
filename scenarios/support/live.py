"""Shared plumbing for the live smokes (scenarios/live): one real app
over one real provider, plus the skip-if-absent checks every module
opens with. A smoke never fails because a server is off or a key is not
set — it skips, and says why."""

import os
from pathlib import Path

import httpx
import pytest

from otaku.paths import Paths
from otaku.settings import config as config_mod
from otaku.settings.files import write_atomic
from scenarios.support.harness import App, launch
from scenarios.support.server import ModelServer


def live_app(
    tmp_path: Path, server: ModelServer, provider_config: config_mod.ProviderConfig, model: str
) -> App:
    """The real app over `provider_config`, set to play `model`. The scripted
    `server` carries only the harness plumbing (its "test" provider);
    the story itself goes to the live endpoint."""
    root = tmp_path / "state"
    paths = Paths.resolve(root)
    paths.ensure_tree()
    providers = {provider_config.name: provider_config}
    write_atomic(paths.config_file, config_mod.Config(providers=providers).to_toml())
    write_atomic(paths.providers_file, config_mod.providers_toml(providers))
    return launch(root, server, spec=f"{provider_config.name}/{model}")


def first_model(url: str, api_key: str = "") -> str:
    """The first model a live /v1 endpoint lists; pytest.skip when the
    server is down or serves nothing."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        data = httpx.get(f"{url}/models", headers=headers, timeout=5.0).json()["data"]
    except Exception:
        pytest.skip(f"no server at {url}")
    if not data:
        pytest.skip(f"{url} lists no models")
    return str(data[0]["id"])


def require_env(name: str) -> str:
    """The env var's value; pytest.skip when it is not set."""
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} is not set")
    return value
