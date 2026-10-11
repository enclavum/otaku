---
paths:
  - "tests/**/*"
  - "scenarios/**/*"
  - "Makefile"
---

# Tests

Every rule in `docs/product_design.md` must be covered — that is the one
place coverage is owed in full, and a new rule arrives with the test that
holds it. Cover the promise, not its current wording: the
framing templates come FROM `prompts.toml`, so the test edits the file and
reads the wire — asserting the built-in text would pass even with the load
path gone.

A test earns its place by covering what RUNNING the app cannot show. Never
assert user-facing copy or screen appearance — a wrong sentence, a missing
blank line, a rule that failed to draw are all seen the moment the app
runs, and pinning them only makes rewording break the suite. Assert what
running it cannot show: the wire, the store, the files on disk, exit
codes, and pure logic with many cases. Where the printed text IS the
payload (`/context` previews the wire verbatim), asserting it is asserting
the payload, not the copy.

Unit tests (`tests/`, `make test`) are written test-first, from the
module's documented contract — never by reading its code, so they check
what the module promises rather than what it happens to do — and ONLY for
pure functions: no disk, network, database, or terminal, the architecture
test the one declared exception. A failure means the implementation is
wrong until proven otherwise. Where a module's test lives, and what
`tests/test_architecture.py` holds, is `docs/code_structure.md`'s
("Tests").

Scenario tests (`scenarios/`, `make scenarios`) play user stories against
the real app through `support/harness.py`, whose docstring is their API:
deterministic and offline, the wire asserted against the scripted
server's recorded requests, the layout `scenarios/__init__.py`'s. In
every test module — units included — the test classes come first (one
class per command or group) and helper functions after them; within a
class, tests follow the story's logical order.

The fast offline suite is `-m "not live and not cli"`: `live` talks to
real providers, `cli` drives the real binary in a subprocess or a pty
(the markers are declared in `pyproject.toml`); `slow` runs only with
`--slow` (`make regression`), whatever `-m` says.
