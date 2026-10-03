"""The operation surface — the functions the frontends call, one module
per area, each function taking the `Session` first: the web API's tags
made structural. One deliberate exception to the 1:1: the sketch's
`session` tag is served by `Session`'s own state plus this package's
`settings` knobs. Everything else in `backend` is substrate: the
session and its launch, the shared vocabulary, the command table, the
document formats."""

from otaku.backend.api import (
    cards,
    lore,
    play,
    providers,
    reports,
    settings,
    stories,
    support,
    transfer,
)

__all__ = [
    "cards",
    "lore",
    "play",
    "providers",
    "reports",
    "settings",
    "stories",
    "support",
    "transfer",
]
