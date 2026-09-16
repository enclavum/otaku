"""THE BACKEND — the one surface the user-driven frontends call.

The map (mirroring the web API sketch):

- `backend.api` — the OPERATIONS: one module per API tag, every function
  taking the `Session` first, its result types beside it. The frontends'
  whole write surface; the boundary is one grep: `backend.api.`
- `backend.session` — `Session`, `Refused`, `TerminalSettings`, the /set
  vocabulary constants.
- `backend.launch` — `open_session` (the front door) and cli's log
  accessors.
- `backend.commands` — the shared command table.
- `backend.formats`, `backend.paths` — substrate the operations drive.

Every name is imported from its home module; re-exported HERE is only
the bridge — the types defined BELOW backend that frontends render or
catch, so that `terminal` and `web` never import a lower package and the
arrow rule stays absolute.

Expected refusals raise `session.Refused`, whose message is the sentence
to show; wording both frontends display verbatim is composed in this
package; the frontends own only the wording about their own medium.

Internal rule: backend modules import each other as
`otaku.backend.<module>` / `otaku.backend.api.<module>`, never a
package root.
"""

from otaku.encryption import EncryptionError
from otaku.providers import (
    KeySource,
    Locality,
    ModelInfo,
    ModelState,
    ProviderConfig,
    ProviderInfo,
)
from otaku.settings.config import ConfigError, TerminalSettings, WebSettings
from otaku.store import DatabaseError
from otaku.store.ops.stories import StoryListing
from otaku.store.schema import Character, Journal, Message, Scene

__all__ = [
    "Character",
    "ConfigError",
    "DatabaseError",
    "EncryptionError",
    "Journal",
    "KeySource",
    "Locality",
    "Message",
    "ModelInfo",
    "ModelState",
    "ProviderConfig",
    "ProviderInfo",
    "Scene",
    "StoryListing",
    "TerminalSettings",
    "WebSettings",
]
