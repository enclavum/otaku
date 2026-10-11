---
paths:
  - "otaku/settings/**/*"
  - "otaku/backend/launch.py"
  - "otaku/backend/domain/app/__init__.py"
  - "otaku/backend/domain/app/settings.py"
  - "otaku/backend/domain/app/providers.py"
  - "otaku/backend/domain/app/integrations.py"
  - "otaku/backend/domain/story/prompts.py"
  - "otaku/backend/api/app/__init__.py"
  - "otaku/agents/__init__.py"
  - "otaku/terminal/screens/models/**/*"
  - "otaku/web/static/js/screens/models.js"
  - "otaku/store/migrations/**/*"
  - "otaku/store/schema.py"
  - "otaku/backend/domain/_formats/**/*"
  - "scripts/rehearse_migration.py"
---

# Configuration files and migrations

## Configuration files

The app writes `configs/config.toml`, `configs/providers.toml` and
`configs/integrations.toml` once at first run, and `configs/prompts.toml`'s
stub whenever the file is absent; after that it only edits them in
place — the settings migrations, and a value a file owns written from
inside the app (a provider's url and key, the web search key, the
context limit, a prompt) — every edit riding `settings.commit`.
`state.toml` and `models.toml` are otaku's own, rewritten whole.
config.toml, state.toml, prompts.toml and models.toml are read as they
stand; providers.toml and integrations.toml are read at launch and held,
the app's own edits changing file and memory together, a hand edit seen
at the next launch. A write that cannot land is refused (`Unavailable`)
and changes nothing; the story to resume is the one best-effort write.

The model in use is one value both frontends read and write
(`api.app.update(model=…)`), as `last_story` is. Lore extraction asks
the model played with unless one is PINNED for it, set by either picker
(the page's "Use for extraction", the terminal's `x`) through
`api.app.update(extraction_model=…)`. A pinned model the engine no
longer serves fails the pass and holds the status — never a quiet fall
back to the play model.

## Migrations

Three compatibility frameworks, one per artifact that outlives a
release — a format change lands inside one of them, never beside them:

- **Settings** (`otaku/settings/migrations`) — convergent, rerun at
  every launch, self-healing.
- **Database** (`otaku/store/migrations`) — the versioned ladder, its
  package docstring the whole of it.
- **Export format** (`otaku/backend/domain/_formats`) — the import
  parser is the upward migration.

Before a release, all three are exercised against REAL artifacts of the
last released version, not only the suites' synthetic fixtures: a state
dir the released build wrote must launch clean (settings converge, the
database ladders up), and a document it exported must import
(`scripts/rehearse_migration.py`).
