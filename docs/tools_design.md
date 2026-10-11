# otaku — tool calls

A model uses a tool by writing a block in its reply. otaku owns the convention: the block is a
fenced code block whose info string names the tool, the reply is read by otaku's own parser, and
the API's `tools` field is never sent. Four tools exist: a question the reader answers, a note
the model keeps for itself, and two that otaku runs and answers itself, system info and web
search.

## The decision

The first reason is what a call is. A question and a note are text the story keeps: the export
carries them, the lore pass leaves them out, the reader sees them in the transcript. A native
tool call puts that text in a JSON field, ends the message with a tool-use stop reason, and wants
a `tool` role row for the answer. None of that is a story row.

The second reason is coverage, confirmed in the engine sources. Ollama refuses a request that
carries `tools` when the model's template has no tools section (`CheckCapabilities` in
`server/images.go` answers "does not support tools"). omlx passes the field to the chat template
and attaches a parser only when the template carries a marker it knows (`[TOOL_CALLS]` for
Mistral, `<|tool_call>` for Gemma 4, and so on, in mlx_lm's `_infer_tool_parser`); a template
without one renders nothing for the field and no call is ever parsed, so the field is silently
dropped. omlx's own docstring calls its tool calling "prompt-driven plus output parsing". Native
tool calling is the same convention with the engine's parser in place of otaku's, and it works
only where the engine has one.

## The models

By template family, as the two engines treat them; what a model's template holds is read with
`ollama show` or from its `chat_template.jinja`.

Ollama:

| Family                     | Template                                            | Native tools               |
|----------------------------|-----------------------------------------------------|----------------------------|
| Gemma 4 and its fine-tunes | Gemma 4 canonical                                   | yes                        |
| Mistral Nemo               | Mistral, Ollama's own template with AVAILABLE_TOOLS | yes                        |
| Nemo roleplay fine-tunes   | ChatML                                              | no, the request is refused |
| Gemma 3                    | Gemma 3                                             | no, the request is refused |

omlx:

| Family                                                              | Native tools                      |
|---------------------------------------------------------------------|-----------------------------------|
| Gemma 4 and its fine-tunes                                          | yes, the gemma4 parser matches    |
| Nemo roleplay fine-tunes, on ChatML or on bare [INST]               | no, the field is silently ignored |
| Mistral Small fine-tunes with the tool branch cut from the template | no, the field is silently ignored |
| Mistral Nemo Instruct with the old tokenizer template, no tools     | no, the field is silently ignored |

Every "yes" is Gemma 4 or stock Nemo. Every Nemo roleplay fine-tune and the Mistral Small ones say
no, and that is the population the product serves.

## The syntax

A call is a fenced code block whose info string is `otk-` and the tool's name, words of letters
joined by single hyphens (`otk-web-search`). A question, always the last thing in the reply:

````
The door creaks on its hinges, and the hall beyond is dark.

```otk-question
Go in?
1. Yes
2. No, wait for Mara
```
````

A note, anywhere in the reply:

````
```otk-note
Toln recognized the seal. Saying nothing yet.
```
The hall glows as she steps in.
````

The grammar is three rules, and the parser keeps one piece of state, the name of the open call:

- A line opening with a fence and an `otk-` info string opens a call. A call already open ends
  first. A plain fence that may be open does not matter, because otaku never tracks plain
  fences: they are prose.
- A bare fence line ends the open call. With no call open it is prose, whatever it opens or
  closes for a markdown viewer.
- A fence with any other info string is prose outside a call and content inside one, as a
  markdown viewer reads it.

The info string carries the namespace: what makes a block a call is the `otk-` prefix and
nothing else, no list of tools consulted, so a call stays one in a build that never heard of its
tool, and a body keeps its fences for good while the tools come and go between builds. Which
tools there are, who answers each, and how its inside is read is the backend's
(`backend.domain.story._tools`); what a call is, is `context`'s. Whether a tool is on, and the prompt that
teaches the model to use it, is a story setting's (`backend.domain.story.settings`).

A tool that is off does not exist for the model: its prompt is not injected, and every call of
it leaves the wire, past ones included — the wire carries the calls of the tools that are on and
nothing else, while the stored body keeps them all. otaku neither poses nor runs a call of a tool
that is off, and the reply does not end at one.

A fence is only a fence at the start of a line, and the closing one stands alone on its line, so
the parser is line-based: a line still arriving is held until its newline decides it. A model
that forgets to close an ordinary fence loses nothing: the next `otk-` fence line opens its call
regardless, and the call reaches the reader. A markdown viewer draws a call as a code box with
the info string hidden, visibly not story; in plain text it is what any reader of markdown
expects.

## Ending the reply

A tool the reader answers ends the reply at its closing fence: the parser reports the piece the
closing fence ended, otaku closes the stream, and whatever arrives after is dropped. The rule is
otaku's, so it is the same on every engine and on the generic provider, and nothing rides the
wire for it: no stop parameter, no cap on how many tools may end a reply, and a model's
reasoning cannot trip it, since the parser reads content deltas alone.

The cost is the usage. It arrives in the stream's final chunk, which a cut never sees, so a reply
that ended on a question has no token counts: its stats line carries the time alone, as a reply
cut by Ctrl+C does. A few tokens are generated before the abort lands, and the engine must honour
a disconnect, which the Ctrl+C path already relies on.

## Tools otaku runs

System info and web search are `Actor.SYSTEM` tools: otaku answers the call itself. A system info
call is empty, and answers the local time and weekday, the moon's phase, the nearest solstice or
equinox and the model's name — nothing that tells where the machine is or what it runs. A web
search call holds the query, and answers the top results' snippets, cut to a character budget,
from the engine the one web search key belongs to; a search that fails answers the sentence that
says why, and the model reads it.

Such a call ends the reply as a question does, and then the play goes on. The reply is recorded
as it stands, ending at the call with its closing fence; otaku runs the tool; the result is
recorded as a turn of its own, role and kind `tool`, the tool's name its provider; and the model
is asked again over the story as it now stands. Three rounds at most: the last result is recorded
but not asked about. A regenerate or a later turn sees what the model saw, because the result is
stored.

On the wire a tool turn is the reader's turn — the wire has two sides, and the result is what the
model is told — enclosed by prompts.toml's `tool_framing`, an OOC frame as shipped. It counts as
none of the reader's messages, the lore pass leaves it out as it leaves the calls out, and the
export leaves it out. Undo and regenerate take a reply together with its tool chain, as one unit.

The loop does not use the API's `tools` field, for four reasons. On omlx it silently does nothing
for every model whose template has no tool branch, and Ollama refuses the request for the same, so a
reader would switch on a feature that works on half their models. A `tool` role exists in neither
the assembler's merge nor the templates: Nemo's raises on a non-alternating role, the ChatML
fine-tunes have no such role — so the stored tool turn rides as the reader's. The tool schemas
render into the head of the prompt, so toggling a tool per story invalidates the prefix cache where
a depth-1 injection costs nothing. And two parsers with two wire shapes for one feature set is a
feature the reader cannot reason about. What the native field buys is that Gemma 4 has seen its own
format in training; the trial showed roleplay models follow a prompted end-of-reply block already,
so it buys nothing even there.
