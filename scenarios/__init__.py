"""User stories played against the real application.

The layout follows the app's own seams:

- `session/` — everything inside an open session, one module per
  `otaku/backend/api` module, classes in `/help` order, with the screen a
  command opens tested beside it.
- `cli/` — one module per top-level command: `test_main.py` (the bare
  invocation, driven in a pty), `test_logs.py`, `test_update.py`. All of
  it carries the `cli` marker, because driving the real binary costs
  seconds where the in-process kind costs milliseconds.
- `web/` — the page over a real session. Its `page` fixture serves on a
  thread and speaks HTTP; the session is opened on THAT thread, because
  a sqlite connection answers only the one that opened it, and the test
  asserts through its own store connection.
- `live/` — the smokes that talk to real providers, one module per
  provider, each skipping itself when its server or api key is absent.
- `test_app.py` — getting a session at all: encryption, backups, resume.
- `fixtures/` — the artifacts a synthetic string cannot stand in for: a
  real SillyTavern chat, a character card PNG, prose with real
  typography, a photograph of a cat for the vision smokes.
- `support/` — the harness, the scripted OpenAI-compatible server, and
  the pty driver.
"""
