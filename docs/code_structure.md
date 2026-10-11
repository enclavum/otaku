# otaku — code structure

How the code is laid out: the layers, the packages inside each, and the rules that keep them apart.
What a module owns is its docstring's; this is the map the docstrings hang on.

## The layers

Each package imports only the packages after its arrow, besides the standard library and the
declared dependencies:

    cli          → terminal, web, backend.launch, logging, update, formatting
    terminal     → console, backend.api, web, formatting
    web          → console, backend.api, formatting
    console      → formatting
    agents       → context, providers, store, logging, formatting
    backend      → agents, context, providers, integrations, store, settings, encryption,
                   logging, formatting
    context      → store (reads only), providers
    providers    → settings, formatting
    integrations → settings, formatting
    store        → encryption
    settings     → formatting
    logging      → providers, encryption, formatting
    encryption   → formatting
    update       → (nothing)
    formatting   → (nothing)

The calls point the same way as the imports: nothing imports upward, and every arrow is exercised by
a call in its own direction. A frontend reaches the backend through `backend.api` and nothing else;
cli adds `backend.launch`, the door it opens the app through. The terminal reaches the web only to
serve the app it holds (`/web`); the web knows nothing of the terminal.

## The backend

    backend/
      api/        the interface: what a frontend calls
      domain/     what otaku does: every rule and every store access
      errors.py   the refusals, raised below and caught above
      launch.py   one state dir opened as the app
      samples/    the stories and pictures a first launch seeds

Inside, imports point one way: `errors` ← `domain.app` ← `domain.story` ← `api` ← `launch`, with
`domain._formats` beside `domain.story`. A module imports another by its own module path, never
through a package root. A package root holds its namesake or nothing: the facade and the vocabulary
its resources share in `api`, the app's own resources in `api.app`, the `App` in `domain.app`,
nothing in either `story`.

## The mirror

Five places are laid out alike — the domain (`backend.domain`), the interface (`backend.api`), each
frontend's api package (`web.api`, `terminal.api`), and the page's api client
(`web/static/js/api/`) — in two packages of twins, `app` for the installation and `story` for a
story and what it holds:

    app/                                    story/
      __init__       the app itself           stories    the stories, the actions on one
      machine        the memory a model fills settings   a story's settings
      settings       the installation's       messages   its messages, a line played
      providers      the providers            lore       its memory
      models         a provider's models,     cards      a character card brought in
                     a model's settings       context    its next request
      integrations   web search, support      pictures   a message's pictures
      history        the lines submitted      transfer   the story in and out as a document
      reports        the tokens, the run,     prompts    what its settings send
                     the accounts             language   the story's language

The five hold the same module list in the same structure: the same two packages, the same modules
in each under the same names, and each package root holding its namesake or nothing. The page's
client writes the list in its own shape: a package is one module (`app.js`, `story.js`) and a twin a
section of it, headed with the twin's name, in the order above — the `app` root's resources its
`app` section, the `story` root's none. A module is added to all five or to none, and a place holds
nothing beside its twins but its own machinery. The terminal's `dispatch` follows the same layout
with a module for each resource that has commands — a resource without one has nothing to carry out
there.

A module imports a twin from the layer below under an alias that names the layer: an interface
module imports `domain_<name>` — any other domain module too — and the terminal imports a module of
its door as `api_<name>`.

The layout is the code's; the call paths are the interface's. A call spells its path through the
facade's attributes (`api.stories.regens.create`) wherever its class lives (`story/stories.py`),
and the HTTP path follows the call path.

## Machinery

A module with no twin is its layer's machinery, and the underscore in its name says so:

    backend.api._writes             a write's answer: Changed, UNCHANGED
    backend.domain._formats         the documents, in and out
    backend.domain.app._paths       what one state dir holds
    backend.domain.app._passwords   the web password's hash
    backend.domain.app._runs        a forced extraction pass, as it runs
    backend.domain.story._chain     a story's chain: appended, popped, assembled
    backend.domain.story._play      the play: a line recorded, its reply streamed
    backend.domain.story._tools     the tools a reply may call
    web.api._request                what a route is handed and hands back
    web/static/js/api/_http.js      the page's HTTP: the head a write names, a refusal, a stream
    terminal.dispatch._files        a typed path, read on this side

Its own layer imports it; the layer above never does, but for the launch, which composes the app out
of the domain's parts. What the layer above needs of the machinery, a twin re-exports: the play's
entry and its events through `domain.story.messages`, a fresh take on a reply through
`domain.story.stories`.

## The interface

`backend.api` is what a frontend calls, in-process, with no transport.

A frontend builds one `Api` over the app it is handed and holds it. The facade has a resource per
top-level path — `app`, `settings`, `tools`, `prompts`, `shared_reminder`, `providers`, `stories`,
`imports`, `usage`, `info`, `balance`, `pictures`, `history`, `integrations`, `language` — and a
sub-resource is an attribute of its parent, so a call spells its URL:
`api.stories.messages.create(5, line=…)` is `POST /api/stories/5/messages`.

A resource class, `<Noun>Resource`, holds the app and nothing else — no story, no cache. Its methods
are the five verbs: `list`, `get`, `create`, `update`, `delete`. The ids of what a method acts on
come first, in path order, and its fields by keyword. An `update` changes the fields it is given:
each defaults to `UNCHANGED`, so `None` can clear one. One method stands outside the five,
`SettingsResource.check_password`.

A read answers data: an entity, or a list. A write answers `Changed[T]`: the thing as it now stands
and the sentence that says what was done, `Changed[None]` where nothing stands. A stream is a
`create` that answers an iterator of events. Nothing answers a live object, a handle to poll, or a
bare `None`.

An entity is a frozen dataclass, defined in its resource's module after the resource classes, every
derived fact a field: a client calls the resources and needs nothing else. The one kind of method an
entity keeps is a text form — a report's `text()`, `Story.landed_line`.

A resource is thin: the domain call, then its entity built — never the store, never an `app._`
member. A builder a sibling module needs is `as_<entity>` (`as_model`, `as_events`), and no
operation.

The root re-exports `Api`, the write's answer, the refusals, the hints, and the vocabulary several
resources share (`Layout`, `StoryMode`, `InjectionPosition`, …). The one exception to "a client
calls the resources" is `story.language`: the story's language as a client applies it to text not
stored yet — a line being typed, a reply still arriving — which no entity can carry.

The app holds no story. Every call that acts on one names it; a call that moves a story's head
names the head the caller last saw (`expected_head`), and the store moves it by compare-and-set — a
head moved meanwhile is `HeadMoved`, and nothing is written. Which story a client is in is the
client's to hold. An operation safe from any thread says so; every other one runs on the app's own
thread.

## The domain

`backend.domain` holds every rule and every store access. It returns its own values — store rows,
small records, its own events — and raises the refusals; a write answers the sentence that says
what it did. It never imports `backend.api`.

A function behind one operation takes the operation's verb and its noun: `get_story`,
`list_messages`, `update_scene` — a bare `list` would shadow the builtin. A named action keeps its
verb: `fork_story`, `undo_exchange`, `regenerate_reply`, `submit_line`, `switch_model`. A field
written alone is `set_<field>`.

The `App` (`domain.app`) is what one opened state dir is made of. Its underscore members are the
domain's: the domain's modules use them, the launch composes them, and nothing above the domain
touches them.

`backend.errors` holds `Refused` and its kinds — `NotFound`, `Conflict` (`HeadMoved` among them),
`Invalid`, `Unavailable` — and the refusal sentences more than one module says.

## A frontend's api package

Each frontend reaches the interface through one package of its own, the only part of it that
imports `backend.api`; the page reaches `web.api` through its own client.

`web.api` is the page's proxy: an HTTP request in, the call out, JSON back. Each twin holds its own
routes; the root merges them into `ROUTES` and `STREAMS` — a path in two modules is a fault at
import — and re-exports the interface's vocabulary, and a twin re-exports the types the server needs
of it (`WebSettings`, `PlayEvent`). `_request` is what every route is handed and hands back.

`terminal.api` is the terminal's door. Its root re-exports the interface's vocabulary; each module
re-exports everything its twin answers and holds what the terminal's own input means to that
resource — a typed argument and the sentence it is answered with (`app.model`,
`app.models.set_think`, `story.stories.title`), a setting's prompt (`story.prompts`), a provider's
field (`app.providers`). A plain call is made on the facade, `Api`; the door adds a function only
where it translates.

The page's api client, `web/static/js/api/`, is the page's door to `web.api`. Each section of
`app.js` and `story.js` holds its resource's routes as the functions the page calls, its own verbs
alone; `index.js` is the facade that hangs every section on its call path — the interface's, so
`api.stories.messages.create(5, …)` is `POST /api/stories/5/messages` — and `_http.js` its
machinery. The rest of the page reaches the server through the facade and nothing else.

## The terminal

    terminal/
      api/         the door
      commands.py  the table: every slash word, the shortcut keys, what makes a line a command
      tty/         the medium itself: escapes, theme, typesetter, rendering, row math
      prompt/      one submission in
      chat/        the conversation as the terminal holds and draws it
      screens/     the full-screen surfaces
      help.py      the /help page
      dispatch/    a typed command carried out
      run.py       the frontend's life, and the prompt → submission loop

Imports point one way: `api` ← `commands` ← `tty` ← {`prompt`, `screens`, `chat`, `help`} ←
`dispatch` ← `run`. A package root holds its namesake or nothing: the interface's vocabulary in the
door's, the routing in `dispatch`'s, the medium's own words in `tty`'s.

`dispatch` is the routing and the handlers. Its root splits a line into its command and its
argument, merges the handlers into one table — a token two modules answer is a fault at import —
and answers the four commands that reach no interface, `/help`, `/web`, `/clear` and `/bye`, as the
web's server holds its own paths. A handler takes the chat and the argument as typed, asks the door,
then draws, lands or opens a screen; it answers the sentence to say, or `None` where it said its
own.

## The web

    web/
      api/       what the page may ask: the routes, as data
      server/    HTTP: the dispatcher, the socket and its hooks, who may ask (`guards`), the two
                 answers that stream (`streams`), what may be served (`assets`), the watch, the
                 sign-in token
      thread.py  the one thread that owns the app
      cert.py    the TLS pair
      run.py     the frontend's life, and the one module that prints
      static/    the page (below)

Three secrets, one home each: `thread` is when work runs, `api` what may be asked, `server` how it
arrives. `thread` imports nothing — its runner is generic over the app it is handed; `api` knows no
thread and no HTTP; `server` is the one that knows both at work, and `run` the one that starts them.

## The page

    web/static/
      app.js           the page's life: what it asks for at boot, and every listener it registers
      js/api/          the client, the page's door to `web.api`
      js/kit/          what every part is built from: elements, spellings, the status line, the
                       browser with its editors and dialogs, a paragraph typeset, a message's
                       pictures
      js/play/         the story as the page plays it: the story it holds, the language, the frame
                       around the transcript, the transcript
      js/screens/      what opens over the page: the story browser, the dossier and its Tools tab,
                       the model picker, the settings slip, the reports, the transfers, help and
                       support, the sign-in
      js/commands.js   what a row of the contents opens, and the doors the play goes through
      js/composer.js   one line in

Imports point one way: `api` ← `kit` ← `play` ← `screens` ← {`commands`, `composer`} ← `app.js`.
The composer stands above the screens because a line it sends goes through `commands`, whose table
opens every screen. A module goes in the group of its role, and imports only what its row in the
architecture test allows.

## The two frontends

The terminal and the page are two media over one interface, and their parts correspond by role, so
a part's twin is found in the other frontend under the same role:

    the page          the terminal             the role
    js/api/           api/                     the door: the one part that reaches the backend
    js/kit/           tty/                     the medium: what every part is drawn with
    js/play/          chat/                    the story as the frontend plays it: held, landed
                                               in, drawn, streamed
    js/composer.js    prompt/                  one line in
    js/screens/       screens/                 what opens over the play
    js/commands.js    commands.py, dispatch/   what a reader's act does
    app.js            run.py                   the frontend's life

Where the job is the same, the two modules are twins under one name: the story browser
(`stories`), the dossier (`dossier`) and its Tools tab (`tools`), the model picker (`models`), help
(`help`), the commands (`commands`). Where the medium shapes the job, each frontend names it in its
own words and the role is the twin: the page's `prose.js` is the terminal's `typography.py`,
`status.js` its `statusline.py`, `held.js` its `Chat`, `shell.js` its `landing.py`, and
`transcript.js` its `stream.py` and `ledger.py` with `render.py` beside them. A part the other
frontend has no use for stays its frontend's own: the page's sign-in, the terminal's simulation of
where printed text leaves the cursor.

What corresponds is the role, never the drawing: what a part means matches across the two, and how
it looks is each medium's own. A rule one frontend copies from the other names its twin's home in a
comment (`kit/prose.js` names `terminal/tty/typography.py`), and a change to what a part means is
made in both. A new part goes in the group of its role; when the other frontend gets it too, it goes
in the twin group there, under the same name where the job is the same.

The order inside each frontend is its own. On the page the composer stands above the screens, since
a line it sends goes through `commands`; in the terminal `prompt` stands beside them, since the run
loop takes the line from the prompt and hands it to `dispatch`.

## A call, end to end

A call keeps its resource's name from the frontend down to the store. Sending a message to story 5
from the page:

    static/js/composer.js            submit: the line typed and sent
    static/js/commands.js            playLine: the line played, then the page landed
    static/js/play/transcript.js     play: the turn drawn as its events arrive
    static/js/play/held.js           writing: a story made where this is its first line
    static/js/api/story.js           messages.create: POST /api/stories/5/messages, If-Match the
                                     head the page last read
    web.server                       the route matched among the streams, the call made on the
                                     app's thread, its events written back as they come
    web.api.story.messages           the row: the line and its pictures read from the body,
                                     If-Match as the expected head
    ──────────────────────────────── both frontends' calls arrive here ────────────────────────────
    backend.api.story.messages       MessagesResource.create(5, line=…): the domain's events
                                     answered as the interface's
    backend.domain.story.messages    submit_line, the play's entry
    backend.domain.story._play       the line checked and recorded, the reply asked of the model
                                     and streamed, then recorded the same way
    backend.domain.story._chain      append_message, from the head the caller saw
    store.ops.stories                StoryOps.append: the message added and the head moved in one
                                     statement
    store.schema                     the messages table

From the terminal:

    terminal.prompt.session          the line typed and submitted
    terminal.run                     submit: no command, so `dispatch` declines it; the pictures
                                     its `@path`s name read; a story made where this is its first
                                     line (`Chat.writing`); chat.api.stories.messages.create on
                                     the facade the door re-exports
    terminal.chat.stream             the reply drawn as its events arrive
    ──────────────────────────────── both frontends' calls arrive here ────────────────────────────
    backend.api.story.messages       MessagesResource.create(5, line=…): the domain's events
                                     answered as the interface's
    backend.domain.story.messages    submit_line, the play's entry
    backend.domain.story._play       the line checked and recorded, the reply asked of the model
                                     and streamed, then recorded the same way
    backend.domain.story._chain      append_message, from the head the caller saw
    store.ops.stories                StoryOps.append: the message added and the head moved in one
                                     statement
    store.schema                     the messages table

The rule is where the two calls converge. Above it each frontend does its medium's part, the page
across HTTP and the terminal in-process, and both end in the same call:
`api.stories.messages.create(5, line=…)` on the interface. Below it there is one path, line for
line the same whichever frontend sent the message. A door that translates stands above the rule
too: the terminal's `/title` goes through `terminal.api.story.stories.title`, which reads what its
typed argument means and makes the `api.stories.update` call itself.

The store is laid out by table, not by resource: one `*Ops` class per table, held by `Store` under
the table's name (`store.stories`, `store.messages`, …), the tables of a kind in one module —
`ops.stories` (stories, messages), `ops.lore` (scenes, characters, journals), `ops.records` (usage,
history, settings). Only the domain touches it, through `app._store`.

## Protected names

The underscore marks what is not part of a surface, at three sizes. A protected module is its
layer's machinery. A protected module-level name is used by nothing outside its module; the moment
another module needs it, it is public. A protected member is its class hierarchy's: a hook a base
declares is filled by its subclasses, and the `App`'s members are the domain's.

## The HTTP surface

Over HTTP the interface keeps its shape: a route for every call path, under `/api`, the method the
verb, a row id in a path digits. A collection answers `{"items": […]}` and a write
`{"value": …, "notice": …}`; an entity made answers 201 and names where it lives in `Location`,
work started answers 202 and names where it is polled. An answer inside a story carries the head the
story stands at as its `ETag` — `"0"` before the first message — but a stream's and a deleted
story's. A write that moves the head names the one it moves from in `If-Match`: 428 without it, 412
with the head where it stands now. Whatever is not a payload is a problem body. `otaku/web/api.yaml`
lists every route.

## Tests

`tests/` mirrors the package tree: a module's test is `test_` and its name, a protected one's
keeping its underscore (`tests/backend/domain/app/test__passwords.py` covers `_passwords.py`), and a
package root's named for its package (`tests/terminal/dispatch/test_dispatch.py`).
`tests/test_architecture.py` reads the source tree and holds the rules a scan of it can check: the
import table, the one-way orders inside the backend, the terminal and the page, the mirror and its
twins, the machinery's privacy, the doors, the resource classes' verbs and answers, the call path
every route follows, and the spec against the routes.

The scenarios that play the app in-process, `scenarios/api/`, are laid out as the interface is:
`app/` and `story/`, a module for each twin a story reaches.
