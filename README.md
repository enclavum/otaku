# otaku — an LLM frontend for roleplay

[![PyPI](https://img.shields.io/pypi/v/otaku.svg)](https://pypi.org/project/otaku/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/enclavum/otaku/blob/main/LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/enclavum/otaku/blob/main/pyproject.toml)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)](https://github.com/enclavum/otaku#requirements)

![otaku web](https://raw.githubusercontent.com/enclavum/otaku/main/images/capture-web.png?v=0.4.0)

Otaku is an LLM frontend for roleplay (similar to SillyTavern, Janitor AI, etc.).

It's free, works on your machine, and lets you play either in a web interface or in the terminal.
LLMs can be local (via llama.cpp, KoboldCpp, Ollama, and others) or accessed via an API service
(OpenRouter, NanoGPT). Otaku needs no infrastructure — no Docker, no database server — installs
in one command, and requires minimal configuration.

## Demo

Demos are available on the [website](https://otaku.sh/):

- Web demo: https://otaku.sh/demo-web/ (optimized for mobile too)
- Terminal demo: https://otaku.sh/demo-terminal/

Both demos include two sample stories and a scripted "model", so you can actually send prompts
and see how the UI works.

## Features

Main features:

- **consistent prose**: the first 20 and the last 150 messages are always sent to the LLM as
  they are, to make it maintain the style;
- **context control**: as the number of messages grows, intermediate messages are split into
  scenes and are automatically replaced by scene summaries;
- **character recognition**: characters you introduce in your story are extracted automatically;
  each character keeps their own journal of what they've seen and experienced;
- **no fixed persona**: you are free to play any character during the story and hint to the LLM
  who is playing whom (the `/me` and `/you` commands);
- **transparent context**: you can see what will be sent to the LLM with the `/context` command.

Import your content:

- **character cards** — into a story you've already started (the `/card` command);
- **SillyTavern chats** — this creates a new story that you can continue (the `/import` command);
- **a text file** — will be split into turns, and you can play with the characters in it (also
  the `/import` command);
- **lorebooks or world info** have no equivalent in otaku, but they can be imported from a file
  into the system message (the `/system` command).

## Requirements

- Platform: macOS / Linux / Windows.
- Local LLM provider(s) **or** an API key for cloud provider(s).

Backends and providers supported: llama.cpp, KoboldCpp, Ollama, oMLX, LM Studio; OpenRouter and
NanoGPT; and any other OpenAI-compatible server through the Generic OpenAI provider, by URL and
key.

## Installation

The script first installs the `uv` package manager — if you don't have it yet — and then
installs otaku with it.

macOS and Linux:

```bash
curl -LsSf https://otaku.sh/install.sh | sh
```

Windows:

```powershell
irm https://otaku.sh/install.ps1 | iex
```

Other ways to install:

```bash
# directly with uv
uv tool install otaku

# alternatively, via Homebrew
brew trust enclavum/tap
brew install enclavum/tap/otaku
```

### Updating

```bash
otaku update
```

The command detects how otaku was installed (uv, brew, etc.) and runs that installer's own
upgrade.

## User guide

Both the web interface and the terminal share the same functions; the difference is that in the
terminal you execute them with slash commands (the reference is available with `/help`), while
in the web interface the operations are available from the menu. In the description below, all
commands are given as they are called from the terminal.

### Launching

In the terminal, type one of the 2 commands:

```bash
otaku          # for terminal
otaku web      # for web interface; default URL is http://localhost:9600
```

On first start, you choose a provider and a model: otaku automatically detects local LLM
backends and lets you pick from their models. Cloud providers (OpenRouter, NanoGPT) are also in
the picker — enter an API key and their catalogs appear — and the Generic OpenAI provider, first
in the picker's panel, takes any other OpenAI-compatible server's URL and key. After you've
chosen (or cancelled with Esc), you land at the prompt. The model picker is available later with
the `/model` (Ctrl+O) command.

To give you an idea of the features and what play looks like, two sample stories are imported on
first start. `/stories` lets you choose one or the other, and `/lore`, `/cast`, and `/context`
show what otaku has built from each.

The command cheatsheet is available with `/help`.

### Starting a story

From there, you can start your own story with the `/new` command. You can also import a
SillyTavern chat with `/import`. Note that importing takes time, because it not only imports
the messages but also extracts characters and scenes from them (more on that below), though you
can cancel the extraction. You can also import a plain text file the same way; it will be split
into messages.

### Playing

You send messages as usual, as your persona; the LLM infers which character to play from the
dialogue. There are helper commands — `/you`, `/me`, and `/ooc` — which only frame your prompt
with minimal injections like "you play as …" (you can see and configure these templates in
`~/.otaku/configs/prompts.toml`).

Mid-prompt, there are also two helper commands: `/ooc` and `/cue`. Both wrap the text after
them in an OOC block; the difference is that the `/ooc` block persists — right for a standing
note to the LLM — while the `/cue` block is sent only once — right for one-time story steering.

A few example prompts:

- `I follow the keeper deeper into the vault.` — plain play
- `/ooc Keep replies under three paragraphs.` — out of character, a standing note
- `"Who goes there?" I whisper. /ooc the keeper does not know me yet` — play with an aside
- `"Come away with me," I tell the keeper. /cue she refuses` — play with a one-time steer
- `/me Keeper: You are late again.` — hint to the LLM that you are playing Keeper now
- `/you Keeper` — tell the LLM to play as Keeper
- `/you Keeper: she is furious` — the same but with a direction

For D&D roleplay, there is the `/roll` command: `/roll 1d20+5 I search the alcove` rolls the
dice, shows you what fell, and sends the result together with your action; `/regen` re-tells the
same roll rather than re-rolling it. Dice examples: `2d6+3`, `d20`, `2d20kh1`/`2d20kl1`.

During play, you can `/undo` (Ctrl+U) the last exchange and `/regen` (Ctrl+R) the last reply.

### Importing character cards

You can import character cards into the story you are playing with the `/card` command, where
you specify a card file to load (either PNG or JSON). The command automatically creates a
character in the lore (see below), and the character greets you. The card is treated as a normal
prompt — it's just that you are sending the card content as your message. The imported card can
later be edited in the `/cast` browser, and the context will change accordingly.

### Managing and forking stories

You can browse the stories you've played with the `/stories` (Ctrl+T) command. From the stories
picker, you can choose a story to continue from any message, or fork from there to another
story. You can also fork a new version of the story you are playing with the `/fork` command.
Forking copies all scenes, characters, journals, etc. to the new branch. Note that you can edit
messages in the stories picker with the `e` key.

### Extracting summaries and lore

After you've sent around 50 messages, a summary pass starts automatically in the background once
you've been idle for 5 minutes, so it doesn't disturb your roleplay. You can also run it on
demand with `/extract`. You'll see a notification and its progress in the status bar, and you
can keep playing meanwhile — replies will just be slower while it runs.

Once the extraction completes, you can browse and edit the extracted summaries and characters
with the `/lore` and `/cast` commands. Summaries are editable, so you can correct them however
you like.

### Understanding the context

The summaries only kick in once you have more than around 200 messages in the chat. The first 20
and the last 150 messages (both configurable) are always sent as is, to preserve maximum detail
of recent story development and your prose style; everything in between is replaced with scene
summaries. Even though summaries may exist up to the latest message, only the older ones are
actually used. Nothing is included in the context by a condition or a trigger word.

The exact context composition, case by case, is described in
[context_design.md](https://github.com/enclavum/otaku/blob/main/docs/context_design.md).
You can use the `/context` command to see what exactly will be sent to the LLM.

### Customizing the web interface

To restyle the web interface, create `~/.otaku/web/custom.css`; it overrides styles in the bundled
design. The custom properties it can set are listed in
[docs/web_tokens.md](https://github.com/enclavum/otaku/blob/main/docs/web_tokens.md).

## Configuration and environments

### Configuration files

Everything lives in the state dir, `~/.otaku` by default:

- `configs/config.toml` — context shape, extraction thresholds, encryption, backups, and web
  settings.
- `configs/providers.toml` — one section per provider (URL, API key). The model picker edits it
  for you, and API keys are stored sealed.
- `configs/prompts.toml` — every template otaku ever sends, editable.
- `configs/state.toml`, `configs/models.toml` — the app's own memory of your session and
  per-model settings.
- `web/custom.css` and `web/fonts/` — web interface customization, if needed.

The config files are written on first run and after that edited only surgically — line by line,
never rewritten as a whole: version migrations at launch and the picker's provider edits, each
keeping the pre-edit file in `configs/backups/`. `prompts.toml` migrates the same way: a
template you left unedited follows a new release's built-in, and one you've edited is never
touched.

Set `OTAKU_CONFIG_DIR` to run a completely separate environment:
`OTAKU_CONFIG_DIR=~/.otaku-alt otaku`.

### Web interface

By default, the web interface is available on the local machine only. You can set it to public by
editing the `host` parameter in the `[web]` section of `~/.otaku/configs/config.toml`. If you do,
you should also consider turning on the next two parameters, `https` and `password`, for privacy.

The web interface can serve over HTTPS. Turn on `https` in the config file and otaku generates a
self-signed certificate, which you can also replace with your own if you need to. Browsers will
show warnings about an untrusted certificate. That is unavoidable with a self-signed certificate,
but the encryption is real.

The web interface can be password-protected — set the password in the same config file.

## Storage and privacy

Stories live in a local SQLite database; the database is snapshotted daily into the state dir,
the last seven kept (configurable).

Encryption at rest is disabled by default but is one config switch away (AES-256-GCM, sealed
client-side): the key can live in your OS keychain, come from a command of your choice (a
password manager, a hardware token), derive from a passphrase, or sit on disk. The request log
is sealed with the same cipher.

Provider API keys are always stored sealed, their key in the OS keychain.

Details in [SECURITY.md](https://github.com/enclavum/otaku/blob/main/SECURITY.md).

## Contributing

See [CONTRIBUTING.md](https://github.com/enclavum/otaku/blob/main/CONTRIBUTING.md) — a small,
focused project; contributions that keep it sharp are very welcome.

## License

[MIT](https://github.com/enclavum/otaku/blob/main/LICENSE).
