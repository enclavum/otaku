"""The key an engine is asked with: which one is in force, where it
came from, the headers that carry it, and whether the server accepts
it. Every engine has one, or none; a catalog's subclass checks the key
against its account, OpenRouter's adds the attribution headers its
service reads.
"""

import enum
import os

from otaku.providers.http import Http
from otaku.settings.providers import ProviderConfig


class KeySource(enum.Enum):
    """Where the key in force came from: the provider's CONFIG (its
    section in the file, or typed in the panel) or the engine's
    environment variable. The config's key wins, since what somebody
    typed beats what the shell carries, and clearing it uncovers the
    variable."""

    CONFIG = "config"
    ENV = "env"


class OpenAIAuth:
    def __init__(self, config: ProviderConfig, env_key: str) -> None:
        """`env_key` names the environment variable a missing key is read
        from, "" for an engine that reads none; it is read once, here."""
        self._config = config
        self._env_api_key = os.environ.get(env_key, "") if env_key else ""

    @property
    def api_key(self) -> str:
        """The key in force: the section's, else the environment's, else ""."""
        return self._config.api_key or self._env_api_key

    @property
    def key_source(self) -> KeySource | None:
        """Where `api_key` comes from — None for no key at all."""
        if self._config.api_key:
            return KeySource.CONFIG
        if self._env_api_key:
            return KeySource.ENV
        return None

    @property
    def headers(self) -> dict[str, str]:
        """What every request carries: bearer auth over the key in force,
        plus whatever the engine's service asks for (OpenRouter's
        attribution). The one door, so a header an engine adds cannot
        miss a call site."""
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def verify_key(self, http: Http) -> None:
        """Raises UnauthorizedError when the server does not accept the
        key in force, UnreachableError when nothing answered. `http` is
        the caller's view: the check spends from the sequence's budget
        and files under its purpose. The base has nothing to ask."""
