---
paths:
  - "otaku/web/**/*"
  - "demo/**/*"
  - "scenarios/web/**/*"
  - "otaku/cli.py"
  - "otaku/backend/app/thread.py"
---

# The web

The web package's layout is `docs/code_structure.md`'s ("The web"), and
the mechanics are in its module docstrings; what follows spans its
modules, the page and the demo, held by `scenarios/web`.

## The one thread

ONE THREAD owns the app, and it is the backend's (`backend.app.thread`):
a handler runs each route as one unit on it (`App.run`) or calls a
`Hooks` callable (`web.server.base`), each safe from any thread, and a
reply's frames are written on the handler's own thread as the events are
handed across. The METHOD is the lane (`web.server`): a route is a GET
exactly when its call only reads, since a read is answered between a
reply's frames and while the model is silent, and the page sends every
read as a GET (`js/api/_http.js`) — a read filed as a POST would queue
behind a reply and leave every screen dead while the model talks.

## The HTTP conventions

- **No request leans on another**: the server keeps nothing between
  two. A two-step exchange sends everything again — a card vetted with
  `?validate_only=true`, then imported by the same request — and work
  that outlives its request is the backend's, polled by a read.
- **A picture rides the play body** as `files: [{name, data}]`, base64
  with no type: the backend sniffs it from the bytes.
- **A row id in a path is digits** (`server._NUMERIC`), so a page that
  lost its story cannot address one. The page's side: the story it
  holds may be null (`held.js`), so a call site guards before building a
  path inside a story and says the backend's sentence, copied.
- **The refusal statuses are one set**, kept in `base._status_of` and
  `_outcome`, `_http.js`'s `OUTCOMES`, `api.yaml` and the demo: a new
  `Refused` kind changes all of them.
- **A refusal and a fault are told apart by `detail` alone**: under
  404/409/412/422/502 a sentence is shown and none means a fault; a 401
  with a sentence is a wrong password, without one a sign-in. So a 404
  fault and the credential guard's 401 never carry a `detail`.
- **A port outside 1–65535 never reaches the bind**, whichever source
  names it: the file's is clamped at load (`settings.config`),
  `OTAKU_WEB_PORT`'s is rejected (`web.run.settings`), `--port` is
  refused by the parser (`cli`) — the address is printed before the
  bind.
- **The state dir's `web/`** (`custom.css`, `fonts/`) is the reader's
  alone, never written by the app; the `--otk-*` tokens and documented
  hooks are a public contract (`docs/web_tokens.md`), a rename a
  breaking change.
- **A file added under `otaku/web/static/`** while otaku runs is served
  only after a restart (the asset table is listed at import); an edit
  needs none.
- **`otaku/web/api.yaml`** is updated by any change to `ROUTES`,
  `STREAMS` or a path the server holds, in the same commit, its
  `info.version` set to the app version the surface last changed in;
  `tests/test_architecture.py` holds the spec against the tables, path
  and method.

## The demos

`demo/web/` MIMICS the page exactly: it is the REAL page with the API
faked in the visitor's browser, so every answer it gives must have the
same SHAPE as the product's — the same fields, the same nulls where a
value is absent, the same status codes and headers (the `ETag` a story's
answers carry, the 412 and 428 its head answers with), the same
refusals, and paths matched the same way (a row id is digits). A field
the product dropped must go; a field it added must arrive. Where the
demo cannot do a thing at all, it says so honestly and points at the
install — but it says it in the product's shape, never a different one.
Checking it is mechanical: capture the real answers over a throwaway
app, ask the demo for the same paths, and diff the structures.

"Rebuild the demos" means otaku.sh's `demos/build.sh <otaku checkout>`,
fixtures included — never a build into this repo, whose `dist/` holds
distribution files alone — and nothing site-specific, no tracker and no
deploy detail, is ever added here.
