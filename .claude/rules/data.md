---
paths:
  - "otaku/store/**/*"
  - "otaku/backend/domain/story/pictures.py"
  - "otaku/terminal/screens/dossier/**/*"
  - "otaku/web/static/js/screens/dossier.js"
---

# The data model and the store

## The data model

`store/schema.py` is the data model, always the current shape, and
`store/migrations` the ladder that brings an old database to it; each
module's docstring says how.

`created_at` / `updated_at` columns are audit fields: no business logic
may ever rely on them. UI display use (e.g. ordering the story list by
recency, "extracted 4m ago") is allowed.

`provider` and `model` name who MADE a row (`schema.py`), and both
frontends show a scene's and a character's where they show a message's:
the page's margin (`dossier.js` `madeBy`), the terminal's preview
(`dossier/base.py` `made_by`).

A message's pictures and a codex card's are files beside the database
(`store/files.py`, named by their `Owner`), coming in through one reader
below both frontends (`backend.domain.story.pictures`), and swept
against what both tables name (`Store.sweep_files`). The daily snapshot
is of the database file alone: the folder is not backed up.

## Store conventions

Method order in every ops class: CRUD first, in order — add, get, list,
update, delete — then getters/setters (the getter before its setter), then
all other methods, privates last.

SQL formatting (`# fmt: off` around each `conn.execute` keeps the
auto-formatter off it; E501 is ignored in `otaku/store/*` for these rows):

- No triple quotes. An INSERT is one string row, `VALUES (...)` included;
  a SELECT keeps FROM on the same row when it reads as one statement.
- When a statement needs more rows, a continuation row never starts with a
  single space — the separator space ends the previous row. The exception
  is indentation, and then it is exactly 4 spaces (CTE inner clauses, a
  SELECT list continued).
- The parameters tuple is one row, right after the SQL.
