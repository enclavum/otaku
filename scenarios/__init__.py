"""User stories played against the real application.

The layout follows the app's own seams:

- `api/` — everything inside the running app, laid out as the interface
  is: `app/`, `codex/` and `story/`, a module for each of its twins a story
  reaches (`story/test_messages.py` a line played and its reply), classes in
  `/help` order, with the screen a command opens tested beside it — and
  `test_meta.py`, the commands no resource answers.
- `cli/` — one module per top-level command: `test_main.py` (the bare
  invocation, driven in a pty), `test_logs.py`, `test_update.py`. All of
  it carries the `cli` marker, because driving the real binary costs
  seconds where the in-process kind costs milliseconds. `test_screen.py`
  is the terminal's screen itself, emulated and held against its own
  echo turn by turn: minutes, so `slow` too, run only with `--slow`.
- `web/` — the page over a real app. Its `page` fixture serves the app
  beside the test (`web.run_beside`) and speaks HTTP, holding the story
  it plays as the page does; the test asserts through its own store
  connection. `test_beside.py` is the page served beside the chat.
- `live/` — the smokes that talk to real providers, one module per
  provider, each skipping itself when its server or api key is absent.
- `providers/` — the providers' transport against a real socket on this
  machine: what a unit test could not hold without binding one.
- `test_app.py` — getting an app at all: encryption, backups, resume.
- `fixtures/` — the artifacts a synthetic string cannot stand in for: a
  real SillyTavern chat, a character card PNG, prose with real
  typography, a photograph of a cat for the vision smokes.
- `support/` — the harness, the scripted OpenAI-compatible server, the
  pty driver, and the emulated screen over it.
"""
