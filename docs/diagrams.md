# otaku - top level diagrams

Two drawings of the same program at two scales: the packages and how
they depend on each other, and the inside of `web`, where one thread and
three secrets need a picture more than the rest of the code does.

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
            │  api · domain · launch  │              │ extraction · warm-up │
            └─┬──┬──┬──┬──┬──┬──┬─────┘              └──┬────┬────┬────┬────┘
              │  │  │  │  │  │  └► integrations         │    │    │    │
              │  │  │  │  │  └───► settings             │    │    │    │
              │  │  │  │  └──────► encryption (unlock)  │    │    │    │
              │  │  │  └─────────► logging ◄────────────┘    │    │    │
              │  │  └────────────► providers ◄───────────────┘    │    │
              │  └───────────────► context ◄──────────────────────┘    │
              │                      │ reads                           │
              └──────────────────► store ◄─────────────────────────────┘

    backend: a frontend reaches it through `backend.api` alone (cli adds
             `backend.launch`); inside, `errors` ◄ `domain.app` ◄ `domain.codex`
             ◄ `domain.story` ◄ `api` ◄ `launch` — `docs/code_structure.md`,
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
    terminal ─► web: /web serves the app the terminal holds to a browser and waits.
             The one arrow between the frontends, and it points only this way — a
             page has nowhere to send anybody back to
    console: what a frontend draws in the terminal it was LAUNCHED from — the
             banner both open with, the tail under the web's. Below both
             frontends because neither may own what the other prints, and a
             leaf because it is HANDED what it draws

## Inside `web`

Four modules and three secrets: `thread` (WHEN work runs), `api` (WHAT
may be asked, as data), `server` (HOW it arrives) — with `run` above all
three, owning the frontend's life and the terminal it prints into.

                        cli ──► web/__init__ ──► run          (re-exports: run, run_shared,
                                                               serve, settings, ServeError)

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
      └────┬─────────┬────┘   check_password           │ run(work,            │ ROUTES · STREAMS
           │         │        (injected, no            │ reading=…)           │ Ask · Created ·
           │         │        import — see below)      │ drain()              │ Accepted · Tagged
           │         └─────────────────────────────────┤                      │ Blob · event
           │  AppRunner()                              ▼                      ▼
           │  .loop(stopping) · .abandon()   ┌──────────────────┐   ┌──────────────────┐
           └────────────────────────────────►│      thread      │   │       api        │
                                             │ ONE thread owns  │   │ what the page    │
                                             │ the app; two     │   │ may ask, as data │
                                             │ queues           │   │                  │
                                             └────────┬─────────┘   └────────┬─────────┘
                                                      │ hands the app        │ Api(app)
                                                      ▼ to the work          ▼
                                                ┌───────────────────────────────┐
                                                │       backend (the app)       │
                                                └───────────────────────────────┘

      run also ─► console (banner, keys, sound, ticker) · api (the slice it
                  listens on) · cert (the TLS pair)
      api  ─► backend.api only — no HTTP, no threads, nothing above it
      thread ─► nothing: its runner is generic over the app it is handed

Three edges are calls WITHOUT imports, which is why the cycle cannot be
seen from the import list alone:

- **`Hooks`** — `run.serve` builds five callables (`show`, `record`,
  `working`, `ring`, `check_password`) and hands them to `bind`. The
  server calls them and never imports `run`.
- **`app.set_on_idle(runner.drain)`** — the APP calls the thread back
  while a reply is only being waited for. Neither knows the other's
  module.
- **`runner.run(work)`** where `work` is `lambda app: call(Api(app),
  ask)` — the api runs inside the thread's loop without either importing
  the other.

And the import chain that settles whether `thread` could be folded into
`run`: `run → server → thread`, plus `run → thread` directly. Fold it and
`server → thread` becomes `server → run`, while `run → server` already
exists — a cycle. It is not accidental: the thread that owns the app
sits BELOW HTTP because HTTP hands work down to it, and the frontend's
life sits ABOVE because it binds the server and prints about it.

### When the thread is called

Five moments, and the middle three are the whole reason the page stays
answerable while the model is talking.

1. **It becomes the main thread, once.** `run.serve` builds the
   `AppRunner`, then gives the main thread to `AppRunner.loop` and puts
   HTTP on a daemon thread beside it. The signal handler goes there too —
   it is the only thread that can take one.
2. **Every API request, from a handler thread.** The handler calls
   `runner.run(work, reading=<method is GET>)` and blocks until the loop
   picks the job up. `reading` chooses which of the two queues it joins,
   and that is the whole of the lane rule.
3. **Every reply, as one long job.** `Streams._pump` writes the entire
   streaming reply — headers, every frame, the final ring — inside one
   job on the app's thread.
4. **Draining, from three directions, all on the app's own thread.**
   The loop between jobs; `_pump` after every frame; and the idle hook
   during the wait for a first token (`app.set_on_idle`: `run`'s
   `runner.drain`, and for each reply `_drain_and_look`, which also
   answers whether the page is still there). Reads are answered in each
   of those gaps, which is why no lock appears anywhere in `thread.py`.
5. **Shutdown.** `runner.abandon()` answers every job still queued, and
   every one still to come, with `StoppingError` — including the handler
   of a keep-alive connection that outlives the serving.
