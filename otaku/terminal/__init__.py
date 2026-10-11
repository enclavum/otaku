"""THE TERMINAL FRONTEND. Everything it asks of the app goes through its
door, `api`, the one package here that imports `otaku.backend.api`; the
rest owns only the medium:

    api/         the door, laid out as the interface is (`app`, `story`):
                 each module re-exports the types its interface twin
                 answers and holds what the terminal's own input means to
                 it — a typed argument, a setting's prompt, a provider's
                 field; a plain call is made on the facade (`Api`)
    commands.py  the table: every slash word, the shortcut keys, and what
                 makes a typed line a command — a leaf every room reads
    tty/         the medium itself, no chat knowledge: the escape
                 vocabulary, theme, typesetter, turn rendering, row math,
                 terminal queries, the pinned row, spinner, clipboard
    prompt/      one submission in: the session, its keys and caret, the
                 slash menus, multiline assembly, the answers, pictures
    chat/        the conversation as the terminal holds and draws it:
                 `Chat`, a story landed in, a reply streamed, the screen
                 ledger
    screens/     the full-screen surfaces: the model picker, the story
                 browser, the dossier
    help.py      the /help page
    dispatch/    a typed command carried out: the routing, the four the
                 terminal answers itself, and a handler module for each
                 resource that has commands, laid out as the door is
    run.py       the frontend's life (`run`, what cli hands the app to)
                 and the prompt → submission loop, as `web.run` is the
                 page's

Inside: api ← commands ← tty ← {prompt, screens, chat, help} ← dispatch
← run — the arrows never point the other way. A package's `__init__`
holds its namesake or nothing: the door's root the interface's
vocabulary, `dispatch`'s the routing, `tty`'s the medium's own words.
"""
