"""Model servers behind the OpenAI wire protocol.

One secret per module: `wire` is the protocol as pure functions and
the types that cross it, `http` the one transport and the only home
of httpx, `errors` what can go wrong, named, `client` the class every
engine subclasses and its hooks, `registry` the lookup, the fan-out
and the probe, `smoothing` the jitter buffer, and `clients/` one
engine per module.

`ProviderConfig` lives in `settings.providers` (provider settings are
settings) and is re-exported here as part of this package's own
signatures; the request log arrives as a sink protocol, and no file is
ever read or written by this package.
"""

from otaku.providers.client import (
    Capabilities,
    Client,
    KeySource,
    Locality,
    ModelInfo,
    RequestSink,
)
from otaku.providers.errors import (
    DeclinedError,
    ProviderError,
    StatusError,
    UnauthorizedError,
    UnreachableError,
)
from otaku.providers.registry import (
    CLIENTS,
    Probe,
    ProbeOutcome,
    ProviderInfo,
    Registry,
    autoconfigure,
    probe,
)
from otaku.providers.wire import (
    THINKING_LEVELS,
    Chunk,
    Image,
    Stats,
    Text,
    Thinking,
    ThinkingLevel,
    WireMessage,
)
from otaku.settings.providers import ProviderConfig

__all__ = [
    "CLIENTS",
    "THINKING_LEVELS",
    "Capabilities",
    "Chunk",
    "Client",
    "DeclinedError",
    "Image",
    "KeySource",
    "Locality",
    "ModelInfo",
    "Probe",
    "ProbeOutcome",
    "ProviderConfig",
    "ProviderError",
    "ProviderInfo",
    "Registry",
    "RequestSink",
    "Stats",
    "StatusError",
    "Text",
    "Thinking",
    "ThinkingLevel",
    "UnauthorizedError",
    "UnreachableError",
    "WireMessage",
    "autoconfigure",
    "probe",
]
