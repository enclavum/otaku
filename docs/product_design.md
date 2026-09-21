# otaku - top level product design

## Stories

Roleplays / chats with an LLM are saved as stories. You start a story, and later you can branch
it from any message into a new one.

## Messages and framing

All messages typed in the prompt input are sent as they are to the LLM. There are several
commands that are shortcuts to common prompts and format the entered prompt — /me, /you and
/ooc. They just take their corresponding prompt injection from ~/.otaku/configs/prompts.toml and
enclose the prompt you input.

## Lore extraction and summaries

When the user is idle for 5 minutes, otaku starts a background process that extracts lore from
the messages that haven't been processed before. What is extracted:

* **Scenes** — messages are grouped into scenes (see settings in ~/.otaku/configs/config.toml),
  excluding imported cards (see below). For each scene, it extracts:
  * the scene summary,
  * the history up to the scene.
* **Characters** — there is a list of characters for each story, so new characters are added to
  the list with their descriptions.
* **Journals** — one for every character present in the scene. For each of them, it extracts:
  * the character's view of the scene,
  * the character's view of the history up to the scene,
  * the last state of the character in the scene.
* **Speaker labels** — messages that are stored in the database are labeled with which character
  spoke.

To account for possible /undo and /regen commands, automatic lore extraction doesn't create
scenes from the last 20 messages. You can also manually trigger the lore extraction by executing
the /extract command.

To view the extracted lore, you can use the /lore and /cast commands.

## Context building

Otaku doesn't rebuild the context, except for replacing older messages with summaries (see
below). Every prompt is appended when it is sent and then keeps its place.

The first 20 and approximately the last 150 messages are always sent to the LLM verbatim, to
maintain the prose style. Messages in the middle are replaced with scene summaries — except for
card prompts (see below) — which keeps the context size low without sacrificing details. You can
see the exact context that will be sent to the LLM with the /context command.

The exact context composition, case by case, is described in [context_design.md](context_design.md).

## Character card import

Cards are imported into each story individually, and there is no central store for them. A story
can hold as many imported cards as needed, and cards can be imported mid-story. With the `/card`
command, the user provides a path to a card file (PNG or JSON) and, optionally, an alternative
name. At import, otaku does three things:

* _the imported card becomes a prompt_: the card's description, message examples, and so on are
  combined into one prompt (there is a template for that), which is then sent as an OOC message;
* the character from the card is created in the characters list, holding the fields the prompt
  was built from — the `/cast` view shows them;
* the greeting from the card is played as the character's own message: the character greets you,
  and the story continues from there.

Card prompts are excluded from scene summarization, and the context always keeps them verbatim.

## SillyTavern chat and plain-text import

Messages can be imported from SillyTavern chats with the /import command, where you provide the
path to the chat JSONL file, or even from a plain text file, where the text is split into
messages automatically. After importing messages, the lore extraction is automatically triggered
in the foreground.

## Database encryption

By default, messages and lore in the database are stored as plain text, but you can enable
encryption by setting a parameter in config.toml.

## Environment separation

You can create a completely separate environment by setting OTAKU_CONFIG_DIR and running the app
this way: `OTAKU_CONFIG_DIR=~/.otaku-alt otaku`.

## Reserved for future versions

Even though extracted, the list of characters, their states, journals, and some other
information are not used directly but are planned to be used in later versions.
