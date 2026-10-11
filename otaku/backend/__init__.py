"""THE BACKEND — what both frontends stand on, reached through ONE
package: `backend.api`. A frontend imports `backend.api` and nothing
else of the backend (cli adds `backend.launch`, the door it opens the
app through).

The map:

- `backend.api` — THE INTERFACE: resource classes over the domain, each
  method one of the five verbs answering with an entity; the story's
  language for text not stored yet (`api.story.language`), the one
  exception; and, re-exported at its root, the vocabulary a frontend
  passes, holds or catches — the refusals, the `App` handle, a write's
  answer, `Changed`.
- `backend.launch` — `open_app`, the door: the composition of one state
  dir into a running app, above the interface (it seeds the samples
  through it); and cli's log accessors.
- `backend.domain` — what otaku does: every rule and every store access,
  below the interface, which it never imports.
- `backend.errors` — the refusal channel: `Refused` and its kinds, the
  shared refusal sentences.

The interface and the domain are laid out alike, in three packages each:
`app`, the installation — the App itself, its settings, the providers
and their models, the integrations, the history, the machine, the
pictures, the reports — `codex`, what every story may draw on and no
story owns, and `story`, a story and what it holds. A module in one has
a TWIN of its name in the other, which an interface module imports as
`domain_<name>`. What has no twin is a layer's MACHINERY, protected by
its name (`api._writes`; `domain._formats`, `domain.app._paths`,
`_passwords`, `_runs`, `domain.story._chain`, `_play`, `_tools`): its
own layer imports it, the layer above never does — the launch, which
composes the app, excepted.

Inside, imports point one way: `errors` ← `domain.app` ← `domain.codex`
← `domain.story` ← `api` ← `launch`, `domain._formats` beside
`domain.story`. Backend modules import each other by their own module
path, never through a package root's re-exports.
"""
