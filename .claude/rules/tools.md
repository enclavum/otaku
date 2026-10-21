---
paths:
  - "otaku/context/tool_calls.py"
  - "otaku/context/injections.py"
  - "otaku/context/assembler.py"
  - "otaku/backend/story/tools.py"
  - "otaku/backend/story/play.py"
  - "otaku/backend/story/messages/*.py"
  - "otaku/backend/story/settings/*.py"
  - "otaku/web/api/story/messages.py"
  - "otaku/settings/prompts.py"
  - "otaku/terminal/tty/render.py"
  - "otaku/terminal/chat/stream.py"
  - "otaku/web/static/js/play/transcript.js"
  - "tests/context/test_assembler.py"
  - "docs/tools_design.md"
---

# Tools and injections

## Tool calls

A model uses a tool by writing a TOOL CALL in its reply, an `otk-NAME`
fenced block (`context.tool_calls`). otaku owns the convention, never the
API's `tools` field, so it works on every engine (`docs/tools_design.md`
is the decision and the engine facts behind it). `tool_calls` lives in
`context` because the worker and the assembler read it below the
backend.

A tool that is OFF does not exist for the model: its prompt is not
injected, its calls are left off the wire (past ones included), the
reply does not stop at one, otaku neither poses nor runs it, and neither
frontend draws it — the reader sees what the model reads.

## The tools

The tools are `backend.story.tools`'. "Answer" is the question
tool's word alone: the user's. A tool otaku runs is handed the whole app
(`run(app)`), so a tool to come finds what it needs.

One pattern names every tool: what is ONE call is singular — the block,
the name, the class (`otk-question`, `question`, `ToolQuestion`) — and
what names the feature is plural — the label, the setting, its injection
and its prompt key (Questions, `allow_questions`, "questions",
`tool_questions_prompt`); an uncountable one reads the same in both
(`otk-web-search`, Web search). The notes are the one exception, kept as
shipped: `otk-note`, Notes, `allow_assistant_notes`.

## The play

The play's events are `play`'s own, told as the interface's by
`as_events` (`backend.story.messages`); the wire names them the same
in snake case (`web.api.story.messages.event`: `user_message`, …). A
reply cut at the length limit keeps its call open.

## How the frontends draw a call

Both frontends draw a tool message as its `tool_label` alone — the result
is the model's to read, not the story's: the page's hover hint, nowhere
in the terminal — and nothing for a call otaku runs, the status row
saying it is being called.

Both frontends draw what the `Story` says: the question it stands on
(`Story.question`) and the tools whose calls are shown (`shown_tools`);
the page also draws those it folds (`folded_tools`) by their label, which
the terminal, with no hover, leaves out. `ToolQuestion._OPTION`'s rule is
copied in the page as `readAsk`, for a question still streaming.

## Injections

An `Injection` is text the context carries besides the story
(`context.injections`). `assemble_story` is ONE function, the store
reads included, so its unit tests hand it a stand-in store. An injection
at the "system" position costs the prompt cache nothing; a numbered one
moves with the end, and the cache mark sits above it
(`assembler`, `providers.openai.requests`).
