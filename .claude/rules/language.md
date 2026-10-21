---
paths:
  - "otaku/terminal/**/*"
  - "otaku/context/syntax.py"
  - "otaku/backend/story/language/*.py"
  - "otaku/backend/story/tools.py"
  - "otaku/web/static/js/play/language.js"
  - "otaku/web/static/js/play/transcript.js"
  - "otaku/web/static/js/composer.js"
  - "otaku/web/static/js/commands.js"
  - "otaku/web/static/js/screens/dossier.js"
  - "otaku/web/static/js/screens/help.js"
  - "tests/test_architecture.py"
---

# The story's language and the commands

## The story's language

`context.syntax` owns the story's typed language, and says why it lives
in `context`; `backend.story.language` is what a client applies of
it to a line not stored yet.

A played line reads in both frontends, in every layout and in the
prompt as typed, as its two kinds of text: the story, and its WORDS WITH
THE MODEL drawn faint. Which is which is the language's (`syntax.spans`;
a stored row's `ooc` runs in `Message.segments`), never a frontend's.
The dossier shows a message as stored.

The directions (`/you`, `/me`, `/ooc`, `/cue`, `/roll`) are sugar for
the player, never an obligation: a design works with none of them
typed, the plain-text path exists and is the primary one, and a
direction may only short-circuit a question it happens to answer.

## The commands

COMMANDS ARE THE TERMINAL'S (`terminal.commands`): the page has none — a
button calls an endpoint — and what both frontends type is the story's
language. A new command is a row in the terminal's table, a handler in
`terminal.dispatch`, and a row of `_ON_THE_PAGE` in
`tests/test_architecture.py` — a screen, an endpoint, or a deliberate gap
with the reason it is one (`/web`: the page is already there); the suite
fails until all three exist.

The `@` sigil in a command argument exists ONLY to trigger path
completion (`terminal.prompt.completion`): every handler that reads a
path strips a leading `@` (`removeprefix("@")`) and never branches on
it. In a PLAYED line, `@path` naming a picture attaches it
(`terminal.prompt.pictures`).
