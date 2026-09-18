"""The web frontend: the page a browser draws over an open session, and
the local server that hands it over.

The medium is the only difference from `terminal` — the command surface
is the shared one (`backend.commands`), and a click on a rail button and
a token typed into the composer are the same command taking the same
path. What the frontend owns is how that looks: `static/` is the design
in its finished form — the token contract in `docs/web_tokens.md`, the
page and its modules beside it.

`cli` resolves the state-dir root and hands it over, exactly as it hands
it to `backend`, and `web.run` is this frontend's whole life over an
open session — as `chat.run` is the terminal's. What this package reads
of the state dir is its own medium and nothing else: where to listen
(the `[web]` slice of config.toml, `web.settings`) and the reader's own
`web/` directory.

Four modules, one job each: `run` is the life (the banner, the tail,
Ctrl+C, and the wiring of everything below); `server` is HTTP; `api` is
what the page may ask, as tables; `thread` is the one that owns the
session.
"""

from otaku.web.run import ServeError, run, serve, settings

__all__ = ["ServeError", "run", "serve", "settings"]
