# The demos

Two demos, one per frontend, built alike: `web/` and `terminal/` hold
the committed sources, the scripts beside them assemble the discardable
builds. Every script starts with a verb and ends with the frontend it
serves; the mechanics live in each script's own header.

- **`build_web.sh <target>`**. The real page (`otaku/web/static`, byte
  for byte) plus the demo's own files, the demo's script tag injected
  into a copy of `index.html`.
- **`build_terminal.sh <target>`**. The real terminal frontend on
  Pyodide: the otaku wheel, the runtime, xterm.js — all self-hosted
  static files, the downloads cached where `DEMO_CACHE` says.
- **`capture_fixtures_web.py`** → `demo/web/fixtures/`, committed. Run
  it whenever the answers the page would receive change — a field added
  or dropped in what `otaku/web/api/` answers, a sample story edited,
  the assembler's context preview reshaped — and commit the diff: the
  fixtures are recordings of the real server's answers, so the diff IS
  the review of what the demo will now claim.
- **`serve_terminal.py <dir>`** — serves a built terminal demo locally
  with the two cross-origin isolation headers SharedArrayBuffer requires
  (in deployment they are the host's to add). There is no `serve_web`:
  the web demo is plain static files, so any static server works —
  `python3 -m http.server -d <target>`.

The terminal demo's offline check is `demo/terminal/smoke.mjs` (a
scripted session on Pyodide under Node); its header says how to run it.

## Building and deploying

The demos have one procedure, and it lives in the site repo: otaku.sh's
`demos/build.sh <otaku checkout>` regenerates the fixtures here, runs
both builds into the site's own folders and smokes the terminal demo.
Nothing is built inside this repo.

Both builds are plain static files and go up as they are. A host owes
the terminal demo one thing beyond serving it: the two isolation
headers on every response under its folder (`build_terminal.sh`'s
header spells them). Whatever a site lays over the pages — its own
headers, its own scripts — it adds after the build; the demos
themselves carry nothing of the kind.
