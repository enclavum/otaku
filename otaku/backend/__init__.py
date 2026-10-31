"""THE BACKEND — what both frontends stand on, reached through ONE
door: the interface — the facade, `backend.api`, and the resources' and
schemas' modules. A frontend imports the interface and nothing else of
the backend (cli adds `backend.launch`, the door it opens the app
through).

The map:

- `backend.api` — THE FACADE: `Api`, the resources hung on their call
  paths, and, re-exported, the vocabulary a frontend passes, holds or
  catches — the refusals, the `App` handle, a write's answer, `Changed`,
  the base classes.
- `backend.base` — what every resource and schema is built on
  (`Resource`, `Schema`, `Report`), and what a write takes and answers.
- The groups: `app`, the installation — the App itself (`app.core`),
  its settings, the providers and their models, the integrations, the
  history, the machine, the pictures, the reports; `codex`, what every
  story may draw on and no story owns; and `story`, a story and what it
  holds. Each holds a RESOURCE FOLDER per resource: its `resources`, the
  classes a frontend calls, each method one of the five verbs; its
  `schemas`, what they answer; and its `services`, the domain — every
  rule and every store access — which imports neither of the other two.
  A resources module imports its folder's services as `domain_<folder>`.
  What serves several folders is MACHINERY, a module beside them in
  their group (`app.paths`, `passwords`, `runs`, `thread`;
  `story.chain`, `request`, `play`, `tools`): services import it, the
  interface never does — the launch, which composes the app, excepted.
- `backend.formats` — the story document and the foreign formats: every
  wild file otaku reads is decoded there.
- `backend.launch` — `open_app`, the door: the composition of one state
  dir into a running app, above the interface (it seeds the samples
  through it); and cli's log accessors.
- `backend.errors` — the refusal channel: `Refused` and its kinds, the
  shared refusal sentences.

Inside, imports point one way: the services in the domain's order,
`errors` ← `app` ← `codex` ← `story`, `formats` beside them; above them
the schemas, then the resources, each group's over the groups below it;
then the facade, the samples and the launch. Backend modules import each
other by their own module path, never through a package root's
re-exports.
"""
