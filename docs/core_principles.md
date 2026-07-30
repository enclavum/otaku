# otaku core principles

## Stories

Roleplays / chats with an LLM are saved as stories. You start a story, but later you can branch it from any message
into a new one.

## Messages and framing

All messages typed in the prompt input are sent as they are to the LLM. There are several commands that are shortcuts
to common prompts and format the entered prompt — /me, /you and /ooc. They just take their corresponding prompt
injection from ~/.otaku/configs/prompts.toml and enclose the prompt you input.

## Lore extraction and summaries

When the user is idle for 5 minutes, otaku starts a background process that extracts lore from the messages that
haven't been processed before. What is extracted:

* Scenes — messages are grouped into scenes (see settings in ~/.otaku/configs/config.toml). For each scene, it extracts:
  * the scene summary,
  * the history up to the scene.
* Characters — there is a list of characters for each story, so new characters are added to the list with their
  descriptions.
* Journals — for each scene and character, it extracts:
  * the character's view of the scene,
  * the character's view of the history up to the scene,
  * the last state of the character in the scene.
* Labeling messages to characters — messages that are stored in the database are labeled by which character spoke.

To account for possible /undo and /regen commands, automatic lore extraction doesn't create scenes from the last 20
messages. You can also manually trigger the lore extraction by executing the /extract command.

To view the extracted lore, you can use the /lore and /cast commands.

## Context

The first 20 and the last approximately 150 messages are always sent to the LLM verbatim, to maintain the prose style.
Messages in the middle are replaced with scene summaries, which helps keep the context size low without sacrificing
details. You can see the exact context that will be sent to the LLM after you enter your prompt with the /context
command.

## Import from ST and free text files

Messages can be imported from ST chats with the /import chat command, where you provide the path to the chat JSONL
file, or from a free text file with the /import text command, where the text is split into messages automatically.
After importing messages, the lore extraction is automatically triggered in the foreground.

## Database encryption

By default, messages and lore in the database are stored as plain text, but you can enable encryption by setting a
parameter in config.toml.

## Environment separation

You can create a completely separate environment by setting OTAKU_CONFIG_DIR and running the app this way:
`OTAKU_CONFIG_DIR=~/.otaku-alt otaku`

## Reserved for future versions

Even though extracted, the list of characters, their states, journals, and some other information are not used
directly but are planned to be used in later versions.

## Current limitations that can be lifted in the future

The app has been tested with local backends only. Using remote backends should be possible, as the app supports the
OpenAI protocol, but this needs additional testing.
