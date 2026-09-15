"""The client: who the engine is and what it owns. The identity is
class knowledge (its id, the panel's caption, where the server runs,
the key's environment variable) plus the configuration. It owns the
`auth` (the key in force), the one transport built on it, and the two
halves, `models` and `completion`, each built from the class the engine
names; the halves are unrelated, read only what they are handed, and
the client is the one that knows both. The account's balance, which
only a catalog has, is the client's own.
"""

import enum
from typing import ClassVar

from otaku.formatting import Money
from otaku.providers.http import ASK_TIMEOUT, ErrorSink, Http
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.completion import OpenAICompletion, RequestSink
from otaku.providers.openai.models import OpenAIModels
from otaku.settings.providers import ProviderConfig


class Locality(enum.Enum):
    """Where a provider's server runs, as far as its client can tell:
    an engine knows, the generic provider is a url and cannot. Every
    reader picks its safe side for UNKNOWN — what costs money or waits
    on the internet treats it as REMOTE, what edits the url treats it
    as LOCAL, and a caption says neither."""

    LOCAL = "local"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class OpenAIClient:
    # ---------- class knowledge: who the engine is ----------

    id: ClassVar[str]  # the engine's, wherever one is named: the registry, a section, a report
    label: ClassVar[str]  # how the provider panel captions it
    locality: ClassVar[Locality] = Locality.UNKNOWN
    # The environment variable a missing key is read from; "" reads none.
    env_key: ClassVar[str] = ""
    # The parts, as the engine implements them.
    auth_class: ClassVar[type[OpenAIAuth]] = OpenAIAuth
    models_class: ClassVar[type[OpenAIModels]] = OpenAIModels
    completion_class: ClassVar[type[OpenAICompletion]] = OpenAICompletion

    def __init__(
        self,
        config: ProviderConfig,
        *,
        request_sink: RequestSink | None = None,
        error_sink: ErrorSink | None = None,
        smooth: bool = False,
    ) -> None:
        self.config = config
        self.auth = self.auth_class(config, self.env_key)
        self._http = Http(config.name, self.auth.headers, error_sink)
        self.models = self.models_class(config, self.auth, self._http)
        self.completion = self.completion_class(
            config, self._http, request_sink=request_sink, smooth=smooth
        )

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        """The engine's default provider section: what the panel shows
        before an engine is configured, and what first run writes for
        the local engines. No key: the environment variable's is read
        at request time, never written into a section."""
        return ProviderConfig(name=cls.id, url="")

    def balance(self, timeout: float = ASK_TIMEOUT) -> Money | None:
        """The account balance as the provider reports it — None where
        there is no account or it will not say. Money, not a rendered
        string: what a reader sees is the frontends' to decide."""
        return None
