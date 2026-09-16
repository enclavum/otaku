"""Model servers behind the OpenAI wire protocol.

One secret per module: `http` is the one transport and the only home
of httpx, `errors` what can go wrong, named, `openai/` the protocol
as a client — `models` the half about models (the listing, the one
model, its facts and state, load and unload), `completion` the half
that is the wire (the streams, the counts), and `client` the identity
every engine subclasses (the section name, the key and its account,
the two halves it owns) — `registry` the lookup, the fan-out and the
probe, `smoothing` the jitter buffer, and `clients/` one engine per
module.

`ProviderConfig` lives in `settings.providers` (provider settings are
settings) and is re-exported here as part of this package's own
signatures; the request log arrives as a sink protocol, and no file is
ever read or written by this package.
"""

from otaku.providers.errors import (
    DeclinedError,
    ProviderError,
    StatusError,
    UnauthorizedError,
    UnreachableError,
)
from otaku.providers.openai import reasoning
from otaku.providers.openai.auth import KeySource, OpenAIAuth
from otaku.providers.openai.client import OpenAIClient, ProviderCapabilities
from otaku.providers.openai.completion import (
    Bounds,
    Chunk,
    OpenAICompletion,
    Reasoning,
    Reply,
    RequestSink,
    Stats,
    Text,
)
from otaku.providers.openai.models import (
    Locality,
    ModelCapabilities,
    ModelInfo,
    ModelState,
    OpenAIModels,
)
from otaku.providers.openai.requests import Image, WireMessage
from otaku.providers.registry import (
    ALL_CLIENTS,
    Probe,
    ProbeStatus,
    ProviderFailure,
    ProviderInfo,
    Registry,
    autoconfigure,
    probe,
)
from otaku.settings.providers import ProviderConfig

__all__ = [
    "ALL_CLIENTS",
    "Bounds",
    "Chunk",
    "DeclinedError",
    "Image",
    "KeySource",
    "Locality",
    "ModelCapabilities",
    "ModelInfo",
    "ModelState",
    "OpenAIAuth",
    "OpenAIClient",
    "OpenAICompletion",
    "OpenAIModels",
    "Probe",
    "ProbeStatus",
    "ProviderCapabilities",
    "ProviderConfig",
    "ProviderError",
    "ProviderFailure",
    "ProviderInfo",
    "Reasoning",
    "Registry",
    "Reply",
    "RequestSink",
    "Stats",
    "StatusError",
    "Text",
    "UnauthorizedError",
    "UnreachableError",
    "WireMessage",
    "autoconfigure",
    "probe",
    "reasoning",
]
