# Changelog

See also the tentative roadmap: [ROADMAP.md](https://github.com/enclavum/otaku/blob/main/ROADMAP.md)

## [0.6.0] - 2026-09-26

General:

- Vision. If the selected model supports vision, you can attach images to your messages — in the
  terminal after the `@` sign, with the paperclip button in the web UI.
- Story tools. A new tab is added to the story dossier, featuring four tools:
  - Questions — when turned on, the model will be instructed how to ask you a question. This also
    makes an assisted roleplay possible: instead of typing a prompt, you pick one of the suggested
    answers.
  - Assistant notes — the model will be instructed to keep private notes between turns, hidden
    unless you choose to see them. This can improve roleplay: the model carries its intentions from
    one turn to the next.
  - Story reminder (aka "Author's notes") — your own text, inserted near the end of the story at
    a depth you choose.
  - Shared reminder — the same but shared between stories.

Web UI:

- The import card action is moved from the main menu to the story dossier.

Backend:

- The banner displayed at the start is reworked and the mascot is replaced with a house mark.

License:

- Changed from MIT to AGPL-3.0.

## [0.5.0] - 2026-09-17

Web UI:

- The web interface can now serve over HTTPS. Turn on `https` in
  `~/.otaku/configs/config.toml` and otaku generates a self-signed certificate, which you can
  also replace with your own if you need to. Browsers will show warnings about an untrusted
  certificate. That is unavoidable with a self-signed certificate, but the encryption is real —
  _you should turn it on if you run otaku over a public network_.
- The web interface can be password-protected — set the password in the same config file.
  _It's also a must if you run otaku over a public network_.
- Edit messages directly in the transcript by double-clicking them. Double-clicking to edit various
  fields now also works in the stories browser, but there, unlike in the transcript, not all fields
  can be edited — look for the edit button.
- Fixed a scrolling issue while an LLM response is streaming.

Mobile:

- The web interface has been optimized for mobile: removed unnecessary elements to free up space on
  screen.
- iPhone: the site can be added to the home screen to launch in full-screen mode. Open the site,
  tap Share → Add to Home Screen → Open as Web App → Add.
- Android and iPad: added a full-screen button.
- Enter starts a new line; the Send button sends.

Backend:

- The providers layer is rewritten from scratch. Models report how their thinking is set — a
  ladder of efforts, an on/off switch, or a budget in tokens — and what else they can do (vision,
  audio, etc.).
- Three more sampling parameters: `top_k`, `min_p` and `repetition_penalty`. Since supported
  parameters vary per provider, the UI shows and lets you edit supported parameters only.
- The thinking level is set at the model level now, not globally as before. The UI shows and lets
  you choose only what the current model takes: its levels, `off`/`on` where it only switches, and
  a number of tokens where the engine holds a thinking budget (0 = off).
- Provider API keys can be set in environment variables (e.g., `OPENROUTER_API_KEY`).
- llama.cpp's router mode: its models listed, loaded and unloaded from the picker.

## [0.4.3] - 2026-09-08

Bug fixes.

## [0.4.2] - 2026-09-06

Bug fixes.

## [0.4.1] - 2026-09-03

- A Generic OpenAI provider - any OpenAI-compatible server should work with it.
- The web UI gets a dark theme switch.

## [0.4.0] - 2026-09-02

- Added web UI: `otaku web` opens the same session in a browser — the second frontend, over the
  same stories, the same lore and the same commands.
- Added native Windows support (10 and 11, 64-bit only).
- The context builder is reworked from the ground up, after `docs/context_design.md`; the new
  `max_context` setting keeps the prompt inside the window models still handle well for roleplay.
- The lore browser in terminal (`/lore`, `/cast`) is reworked to include the system message and
  the story messages.
- Prompt caching for OpenRouter.
- Sound notifications.
- A second, longer sample story.

## [0.3.0] - 2026-08-17

- New feature: character card import with the `/card` command.
- Commands `/me` and `/you` suggest and autocorrect cast names.
- New commands available mid-prompt only: `/ooc` and `/cue`, both hinting the LLM — `/ooc`
  introduces a standing note, `/cue` a one-time steer that the LLM won't see later.
- The browsers (`/stories`, `/lore`, `/model`) follow the terminal theme.
- Commands get highlighted.

## [0.2.2] - 2026-08-08

- New providers: OpenRouter and NanoGPT (cloud), llama.cpp and LM Studio (local); the prompt for
  cloud providers is `$` instead of `>`.
- Numerous UI improvements, including:
  - dialogue coloring;
  - undo erases the taken-back exchange from the screen;
  - regenerate erases the old reply and streams the new one in its place;
  - a rule drawn across the screen wherever the played story breaks;
  - providers configurable directly in the model picker (`/model` or Ctrl+O);
  - `/system` accepting a file in addition to text input.
- New commands: `/clear`, `/last`, `/balance`; command `/rename` renamed to `/title`.
- New CLI command: `otaku update`.

## [0.2.1] - 2026-08-01

Packaging only — no functional changes.

- The required Python version is now 3.11, down from 3.14.
- Dependency bounds updated.

## [0.2.0] - 2026-08-01

Ground-up rewrite as a roleplay terminal client: chats are stories that can be branched from any
message, a background pass extracts lore from played messages — scenes, characters, journals —
and the context sent to the model keeps the opening and the recent tail verbatim with scene
summaries in between.

## [0.1.1] - 2026-07-06

Initial public release.
