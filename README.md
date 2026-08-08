# otaku — a roleplay terminal client

[![PyPI](https://img.shields.io/pypi/v/otaku.svg)](https://pypi.org/project/otaku/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/enclavum/otaku/blob/main/LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/enclavum/otaku/blob/main/pyproject.toml)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)](https://github.com/enclavum/otaku#requirements)

Stories that branch and grow their own lore — on your machine, with optional at-rest encryption.

![otaku demo](https://otaku.sh/demo.gif)

## What it is

Otaku is an attempt to build a terminal alternative to SillyTavern (ST), with a focus on:
- transparency about what is sent to the LLM (the `/context` command),
- automatic incremental summaries that replace the middle of the chat to save context space
  (browse and edit them with the `/lore` command),
- automatic character extraction from the chat (the `/cast` command),
- minimal to no under-the-hood prompt injection.

How the otaku workflow differs from ST (partly limitations of the current version, partly
intentional):
- no pre-created character cards, worlds, lore, etc. — everything is inferred and extracted from
  the chat;
- however, you can set up your world or characters manually in the system message (the `/system`
  command).

Other features:
- importing chats from ST, with scene and character extraction,
- importing a plain text file, parsed into turns, with scene and character extraction,
- loading and unloading models in Ollama, oMLX, and LM Studio directly from the app,
- cloud providers (OpenRouter, NanoGPT) next to the local ones — API keys stored sealed,
- automatic daily backups,
- optional encryption,
- and more.

## Install

```bash
# either with uv
uv tool install otaku

# or via Homebrew
brew install enclavum/tap/otaku
```

Update from version 0.2.1 or earlier:

```bash
# if installed with uv
uv tool upgrade otaku

# if installed via Homebrew
brew upgrade enclavum/tap/otaku
```

Since version 0.2.2, otaku updates itself: `otaku update` detects how it was installed and runs
that installer's own upgrade.

```bash
otaku update
```

## Get started

```bash
otaku
```

On first start, you choose a provider and a model: otaku automatically detects local installations
of Ollama, oMLX, LM Studio, llama.cpp, and KoboldCpp and lets you pick from their models. Cloud
providers (OpenRouter, NanoGPT) are added right there in the picker — enter an API key and their
catalogs appear. After you've chosen, you land at the prompt. If nothing is running yet, otaku
opens anyway — pick a model later with `/model`.

To give you an idea of the features and what play looks like, on first start a sample story is
imported, and you land right in the middle of it. You can explore it with the `/lore`, `/cast`,
and `/context` commands.

From there, you either start your own story with the `/new` command or import an ST chat with
`/import`. Importing takes time, because it doesn't only import the messages — it also extracts
characters and scenes from them (more on that below). You can also import a plain text file the
same way; it will be split into messages. The format is detected from the file, and the extension
has to match: `.jsonl` for an ST chat, `.txt` for plain text, `.md` for an otaku export.

```
/import ~/stories/the-long-road.md  # an otaku export, memory included
/import ~/chats/my-st-chat.jsonl    # a SillyTavern chat
/import ~/drafts/story.txt          # plain text, split into turns
```

## Features

### The play, stories, and branches

You send messages as usual, as your persona; the LLM infers which character to play from the
dialogue. There are three helper commands — `/you`, `/me`, and `/ooc` — which only frame your
prompt with minimal injections like "you play as …" (you can configure these templates in
`~/.otaku/configs/prompts.toml`).

During play, you can `/undo` and `/regen` the last message. You can branch a new version of the
story with `/fork`, or start a new story with `/new`. The `/stories` command lists your stories
and their messages; you can switch to a previously played story from there, and resume it from any
message. If you don't like an earlier message, you can also edit it in the `/stories` view.

### Summaries and character extraction

After you've sent around 50 messages, a summary pass starts automatically in the background once
you've been idle for 5 minutes, so it doesn't disturb your roleplay. You can also run it on demand
with `/extract`. You'll see a notification and its progress in the status bar, and you can keep
playing meanwhile — replies will just be slower while it runs. Once it completes, you can browse
and edit the extracted summaries and characters with the `/lore` and `/cast` commands. Summaries
are editable, so you can correct them however you like.

### How the context is constructed

The summaries only kick in once you have more than around 200 messages in the chat. The first 20
and the last ~150 messages (both configurable) are always sent as-is, to preserve maximum detail
and your prose style; everything in between is replaced with scene summaries. So even though
summaries may exist up to the latest message, only the older ones are actually used.

## Warnings, limitations, and planned features

Beware, this is an early alpha. The full-screen pickers assume a light terminal theme — the play
screen itself adapts to dark ones. Features planned for the next versions:

- Properly wire the characters and lore into the roleplay context, alongside the scene summaries.
  Even though they are extracted, they are not yet injected anywhere into the prompt — they are
  only used to build each character's journal for subsequent scenes. How to use them better is
  still an open question.
- Implement proper multi-chats, with different characters optionally backed by different LLMs.
- Import character cards from SillyTavern.

## Usage

Type to play — your words go to the model verbatim, and the reply streams back as markdown.
Around that:

```
PROMPT     your character speaks or acts             /lore      the memory browser (Ctrl+L)
/undo      take back the last exchange (Ctrl+U)      /cast      the same browser, on the cast
/regen     a fresh take on the reply (Ctrl+R)        /extract   run the summary pass now
/stories   browse and resume (Ctrl+T)                /context   preview the next request
/model     switch models mid-story (Ctrl+O)          /help      everything else
```

## Configuration

Everything lives in the state dir, `~/.otaku` by default:

- `configs/config.toml` — context window, extraction thresholds, encryption, backups.
- `configs/providers.toml` — one section per provider (url, api key). The model picker edits it
  for you, and api keys are stored sealed.
- `configs/prompts.toml` — every template otaku ever sends, editable.
- `configs/state.toml`, `configs/models.toml` — the app's own memory of your session and
  per-model settings.

The two config files are written on first run and after that edited only surgically — line by
line, never rewritten as a whole: version migrations at launch and the picker's provider edits,
each keeping the pre-edit file in `configs/backups/`.

Set `OTAKU_CONFIG_DIR` to run a completely separate environment:
`OTAKU_CONFIG_DIR=~/.otaku-alt otaku`.

## Privacy and storage

Stories live in a local SQLite database. Encryption at rest is disabled by default but is one
config switch away (AES-256-GCM, sealed client-side): the key can live in your OS keychain, come
from a command of your choice (a password manager, a hardware token), derive from a passphrase,
or sit on disk. The request log is sealed with the same cipher; the system and error logs
are content-free by contract.

Provider API keys are always stored sealed, their key in the OS keychain. Daily database backups
are kept in the state dir.

Details in [SECURITY.md](https://github.com/enclavum/otaku/blob/main/SECURITY.md).

## Provider support

| Backend    | Autodetected          | Load / unload models |
|------------|-----------------------|----------------------|
| llama.cpp  | yes                   | -                    |
| KoboldCpp  | yes                   | -                    |
| Ollama     | yes                   | yes                  |
| oMLX       | yes                   | yes                  |
| LM Studio  | yes                   | yes                  |
| OpenRouter | API key in the picker | -                    |
| NanoGPT    | API key in the picker | -                    |

## Requirements

- macOS or Linux, and a terminal
- Python 3.11+ (installed automatically by either installer above)

## Contributing

See [CONTRIBUTING.md](https://github.com/enclavum/otaku/blob/main/CONTRIBUTING.md) — a small, focused project; contributions that keep it
sharp are very welcome.

## License

[MIT](https://github.com/enclavum/otaku/blob/main/LICENSE).
