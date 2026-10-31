# otaku - top level diagrams

Two drawings of the same program at two scales: the packages and how
they depend on each other, and the inside of `web`, where the calls no
import shows need a picture more than the rest of the code does.

The RULES live in `docs/code_structure.md` — the layer table above all,
which `tests/test_architecture.py` holds. What is here is only the shape
of them.

## The packages

This mirrors the layer table in `docs/code_structure.md`. It carries
both graphs at once, dependency and control flow, because they point the
same way everywhere: no upward import, and no import that is not
exercised by a call in its own direction. If the two ever disagree, the
table is right and this drawing is stale.

Legend: a solid arrow between packages = imports AND calls; arrows from
the margin = stimuli (user, browser, idle), not imports; the captions
under the graph state the edges ASCII cannot draw cleanly.

    cli ─┬─► terminal ─────────┐        cli also: ─► logging (viewing) · ─► update
         └─► web (◄─ browser) ─┤                  ─► backend.launch (opens the app)
             both ─► console   ▼
            ┌─────────────────────────┐   schedules  ┌──────────────────────┐
            │         backend         │ ───────────► │        agents        │ ◄── idle deadline
            │ api · groups · launch   │              │ extraction · warm-up │
            └─┬──┬──┬──┬──┬──┬──┬─────┘              └──┬────┬────┬────┬────┘
              │  │  │  │  │  │  └► integrations         │    │    │    │
              │  │  │  │  │  └───► settings             │    │    │    │
              │  │  │  │  └──────► encryption (unlock)  │    │    │    │
              │  │  │  └─────────► logging ◄────────────┘    │    │    │
              │  │  └────────────► providers ◄───────────────┘    │    │
              │  └───────────────► context ◄──────────────────────┘    │
              │                      │ reads                           │
              └──────────────────► store ◄─────────────────────────────┘

    backend: a frontend reaches it through the interface alone — `backend.api`
             and the resources' and schemas' modules (cli adds `backend.launch`);
             inside, a resource folder per resource in the groups `app`, `codex`,
             `story`; the services `errors` ◄ `app` ◄ `codex` ◄ `story`, then the
             schemas, the resources, `api` ◄ `launch` — `docs/code_structure.md`,
             "The backend"
    sealing: store · logging ─► encryption — the DATA plane (one session cipher
             for the database and the request log, BY DESIGN); backend ─► encryption
             covers both planes (data unlock + the api-key plane); keys are wired
             only in backend.launch
    formatting: stdlib-like leaf — anyone above may use it (its arrows are not drawn);
             providers · integrations ─► settings (their sections' types live there);
             context ─► providers (the wire's types); logging ─► providers
    dashed, injected at composition (no import): the worker's status ─► a frontend's
             repaint · ask_secret ─► a frontend
    terminal ─► web: /set serve serves the app the terminal holds beside its chat.
             The one arrow between the frontends, and it points only this way — a
             page has nowhere to send anybody back to
    console: what a frontend draws in the terminal it was LAUNCHED from — the
             banner both open with, the tail under the web's. Below both
             frontends because neither may own what the other prints, and a
             leaf because it is HANDED what it draws

## Inside `web`

Three modules and two secrets: `api` (WHAT may be asked, as data) and
`server` (HOW it arrives) — with `run` above both, owning the frontend's
life and the terminal it prints into. WHEN work runs is not the web's:
the app has a thread of its own, the backend's (`backend.app.thread`),
and every call is handed to it.

                        cli ──► web/__init__ ──► run          (re-exports: run, run_beside,
                                                               settings, Server, ServeError)

      user's terminal                                          browser
            │                                                     │
            │ banner · tail · ctrl+c                              │ HTTP
            ▼                                                     ▼
      ┌───────────────────┐   binds, hands Hooks   ┌──────────────────────────────┐
      │       run         │ ─────────────────────► │           server             │
      │ the frontend's    │                        │  bind · Server · Handler     │
      │ life; the ONLY    │ ◄───────────────────── │  guards · streams · assets   │
      │ module that       │   Hooks: show · record │                              │
      │ prints            │   working · ring ·     └───┬──────────────────────┬───┘
      └───────────────────┘   check_password           │ app.run(work,        │ ROUTES · STREAMS
                              (injected, no            │ reading=…)           │ Ask · Created ·
                              import — see below)      │                      │ Accepted · Tagged
                                                       │                      │ Blob · event
                                                       │                      ▼
                                                       │            ┌──────────────────┐
                                                       │            │       api        │
                                                       │            │ what the page    │
                                                       │            │ may ask, as data │
                                                       │            └────────┬─────────┘
                                                       │                     │ Api(app)
                                                       ▼                     ▼
                                                ┌───────────────────────────────┐
                                                │ backend: the app, on its own  │
                                                │ thread — every call queued,   │
                                                │ in two lanes                  │
                                                └───────────────────────────────┘

      run also ─► console (banner, keys, sound, ticker) · api (the slice it
                  listens on) · cert (the TLS pair)
      api  ─► backend.api only — no HTTP, no threads, nothing above it

Two edges are calls WITHOUT imports:

- **`Hooks`** — `run._bind_server` builds five callables (`show`, `record`,
  `working`, `ring`, `check_password`) and hands them to `bind`. The
  server calls them and never imports `run`.
- **`app.run(work)`** where `work` is `lambda: call(api, ask)` — a route
  runs as one unit on the app's thread, the backend knowing nothing of
  the web's tables.

### When the app's thread is reached

Four moments, and the middle two are the whole reason the page stays
answerable while the model is talking.

1. **Every API request, from a handler thread.** The handler calls
   `app.run(work, reading=<method is GET>)` and blocks until the app's
   thread picks the job up. `reading` chooses which of the two lanes it
   joins, and that is the whole of the lane rule.
2. **Every reply, as one long job.** The stream verb queues the whole
   turn as one job on the app's thread (`App.stream`); `Streams._pump`
   writes the frames on the handler's own thread as the events are
   handed across, and a watcher beside it closes the reply when the page
   goes or the server stops — during the model's silence too.
3. **Draining, on the app's own thread.** Between jobs, between a
   reply's events, and while a job waits on the model (`AppThread.idle`)
   the reads queued meanwhile are answered — which is why no lock guards
   the app.
4. **Shutdown.** `run` returns once the server is shut; closing the app
   answers every job still queued with `StoppingError` (`App.close`).
