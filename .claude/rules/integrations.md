---
paths:
  - "otaku/integrations/**/*"
  - "otaku/providers/**/*"
  - "otaku/settings/integrations.py"
  - "otaku/settings/migrations/integrations_file.py"
  - "otaku/backend/domain/app/integrations.py"
  - "otaku/backend/domain/app/providers.py"
  - "otaku/backend/domain/app/reports.py"
  - "otaku/backend/api/app/integrations.py"
  - "otaku/web/api/app/integrations.py"
  - "otaku/backend/domain/story/_tools.py"
  - "otaku/terminal/screens/dossier/tools.py"
  - "otaku/web/static/js/screens/tools.js"
  - "otaku/agents/**/*"
---

# Providers and integrations

## Integrations

What otaku asks for something other than a completion is the
`integrations` package, laid out as the providers are: one registry,
its own file (`configs/integrations.toml`, typed in
`settings.integrations`), the keys sealed and resolved alike. The launch
binds them into ONE value (`Integrations.bind`) handed to the app, which
never reads a section; a section no integration is named for is passed
over in silence, where a provider's is reported.

No search engine is named outside `integrations/web_search.py` — not in
the file, the api or a frontend's facts; the page's key dialog names
them as copy alone. Both frontends give the key ONE box on the Tools
tab's web search row (the page's `keyButton`, the terminal's `key`
field), over `api.integrations.web_search`. What is asked of an
integration and what is made of its answer is the asker's: the web
search tool composes what the model reads.

## Locality

Where a server runs is `providers.Locality`, whose docstring holds each
reader's safe side for `UNKNOWN`: a model's place is
`client.locality_of(model)`, a provider's `client.locality`. A new
reader of the flag decides the same way, in both frontends, and the enum
has no boolean shortcut on purpose.

## Pictures on the wire

Where a picture rides is answered once, `client.pictures_ride(model)`,
and asked alike by the assembler (through `_request.assemble`) and the
lore pass (`extraction._scene_pictures`); an engine's quirk lives on its
client (`OpenAICompletion.pictures_ride`), never as a capability.
