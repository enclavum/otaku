"""The web frontend: the page a browser draws over the app, and
the local server that hands it over.

The medium is the only difference from `terminal` — every command the
terminal types is reached here through a screen or an endpoint, and the
one thing both frontends TYPE, the story's language, is the backend's
(`backend.story.language`). What the frontend
owns is how that looks: `static/` is the design
in its finished form — the token contract in `docs/web_tokens.md`, the
page and its modules beside it.

`cli` opens the app and hands it over, and `web.run` is this
frontend's whole life over it — as `terminal.run.run` is the terminal's;
`web.run_beside` serves an app the terminal holds, beside its chat
(`/set serve`). What this package reads
of the state dir is its own medium and nothing else: where to listen
(the `[web]` slice of config.toml, `web.settings`) and the reader's own
`web/` directory.

Four parts, one job each: `run` is the life (the banner, the tail,
Ctrl+C, and the wiring of everything below); `server` is HTTP; `api` is
what the page may ask, as tables; `cert` is the TLS pair it serves
under. The thread that owns the app is the backend's (`App.run`).
"""

from otaku.web.run import ServeError, Server, run, run_beside, settings

__all__ = ["ServeError", "Server", "run", "run_beside", "settings"]
