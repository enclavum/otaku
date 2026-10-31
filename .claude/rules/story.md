---
paths:
  - "otaku/backend/story/settings/*.py"
  - "otaku/backend/story/prompts/*.py"
  - "otaku/backend/story/play.py"
  - "otaku/backend/story/chain.py"
  - "otaku/backend/story/request.py"
  - "otaku/backend/story/messages/*.py"
  - "otaku/backend/story/context/*.py"
  - "otaku/backend/app/models/*.py"
  - "otaku/backend/story/stories/*.py"
  - "otaku/context/assembler.py"
  - "otaku/providers/openai/templates.py"
  - "otaku/store/schema.py"
  - "otaku/settings/prompts.py"
  - "otaku/web/api/story/settings.py"
  - "otaku/web/api/story/prompts.py"
  - "otaku/terminal/api/prompts.py"
  - "otaku/terminal/dispatch/settings.py"
  - "otaku/terminal/screens/dossier/tools.py"
  - "otaku/terminal/chat/**/*"
  - "otaku/terminal/tty/render.py"
  - "otaku/terminal/tty/statusline.py"
  - "otaku/terminal/prompt/caret.py"
  - "otaku/terminal/prompt/keys.py"
  - "otaku/web/static/js/play/shell.js"
  - "otaku/web/static/js/play/transcript.js"
  - "otaku/web/static/js/screens/tools.js"
  - "otaku/web/static/js/composer.js"
---

# A story's settings and its layout

## Story settings

What a user sees as features are, below the frontends, a STORY's
settings: one switch each, one class each in
`backend.story.settings.services`, which says what each injects and when.
One setting is a VALUE rather than a switch: the LAYOUT the story is
read in (`novel`, `canvas`, `manuscript`, `chat`). Two facts derive from
it — the look (`mode`) and the wire (`completion_type`) — and the
frontends read the facts, never the layout, except to show and set it.
The mode is the layout's, never a setting of its own: every behaviour is
the layout's, its mode's or the wire's. The page draws the look off
`data-transcript` (`play/shell.js`); the terminal draws it as one text
(`render.flow`) or as blocks.

## The canvas and the join rule

THE CANVAS, on either wire, is the story as one text. Every row stores
the mode it was written in (`messages.mode`), and a row's mode decides
ONE thing: its join, by `assembler.get_join_type` — one rule for the
wire and both canvases, reaching the frontends as data
(`Message.join_type`, and the reply's first piece's), so neither keeps a
rule of its own. Both frontends send a canvas line as typed, and the
backend settles its edges. An EMPTY line is the CONTINUE in the canvas:
both frontends let it through there, and the backend refuses it
elsewhere. In the canvas neither frontend draws a block — the thinking,
the notes, a past question, a tool's message — but the question waiting
to be answered.

The page's canvas is typed in at its end, the TAIL
(`transcript.showTail`), placed by the backend's `Story.next_join`; the
terminal's story mode keeps the tail's keys (`prompt.keys`).

## The text wire

THE TEXT WIRE (`CompletionType.TEXT`) is the novel's alone: the same
assembly as the chat wire's — the recap, the injected rows, a tool's
rows — rendered as one text in the model's own turn markers
(`AssembledPrompt.as_text`). The templates, and which one a model gets,
are `providers.openai.templates`'.

## A setting as a frontend sees it

Every setting reaches a frontend as the same row, the interface's
`StorySetting` (`backend.story.settings`), so a frontend draws them
alike. A settings write moves no head, so it names none. Both Tools tabs
keep one caption table, `_ABOUT`, in the same order and groups —
`terminal/screens/dossier/tools.py` and `web/static/js/screens/tools.js`:
change one, change both.

An injection setting's PROMPT is "prompt" on the backend and the wire,
whatever a frontend calls it (the page's "Prompt", the terminal's
"[ instructions ]"); `backend.story.prompts` says how each is
reached.

One story's settings are the database's because they must fork and die
with the story; the export carries none. A setting stories SHARE is the
`settings` table's when it is a text that may need sealing, a config
file's otherwise (`store/schema.py`); `history` is the precedent.

## The context fill

How full the next request is (`api.stories.context.fill`) is shown by
both frontends after every landing — the terminal's status row, the
page's provider line — and the window alone while it is refused.
