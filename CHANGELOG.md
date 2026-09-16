# Changelog

All notable changes to otaku are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and otaku follows
[Semantic Versioning](https://semver.org/) — while pre-1.0, minor releases may include breaking
changes.

## [0.5.0] - [Planned]

**TL;DR**

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

Full list of changes: [CHANGELOG.md](https://github.com/enclavum/otaku/blob/main/CHANGELOG.md)

Tentative roadmap: [ROADMAP.md](https://github.com/enclavum/otaku/blob/main/ROADMAP.md)

### Added

- Provider API keys can come from the environment: `OPENROUTER_API_KEY`, `NANOGPT_API_KEY`,
  `LMSTUDIO_API_KEY`, `OMLX_API_KEY`, `OLLAMA_API_KEY`, `LLAMACPP_API_KEY`, `KOBOLDCPP_API_KEY`,
  `GENERIC_API_KEY`. A key typed into the panel wins; the variable is never written to the file.
- The llama.cpp and KoboldCpp sections are first written with the port the running server was
  launched with.
- llama.cpp in router mode (`llama-server --models-dir`): its models are listed, loaded and
  unloaded from the picker.
- `/info` reports what the model can do: vision, text completion, and how its thinking is set —
  the levels, `on / off`, `token budget` — or `unknown` where the engine cannot say.
- Three more sampling parameters: `top_k`, `min_p` and `repetition_penalty`.
- `stop` takes several strings, typed as JSON strings: `/set parameter stop "\nUser:" "END"`.
- A provider failure is filed in the error log with its traceback.
- `otaku web` serves over HTTPS with `https = true` in `[web]`: a self-signed certificate is
  generated into the state dir's `cert/`, or your own pair goes there.
- `password` in `[web]` protects the page. A sign-in lasts 4 hours, or 30 days with "stay signed
  in".
- `otaku web` quits on Ctrl+D as on Ctrl+C, and restarts on fresh sources on Ctrl+R.

### Changed

- The thinking level is the model's, kept in `models.toml`; the level `state.toml` held moves to
  the remembered model at the first launch. `/set think` offers only what the model takes: its
  levels, `off`/`on` where it only switches, a number of tokens where the engine holds a budget
  (0 = off). `unset` replaces `default` and sends nothing.
- `/set parameter` offers only what reaches the model in use; any known parameter can still be
  set, and one the provider does not read is kept for the model and said so. The bounds are the
  engine's own: a local engine takes any temperature.
- A model's context is two figures: its own maximum and what the loaded instance serves. A turn
  on a model that is not loaded loads it first, so the prompt is cut to the real window.
- A reply cut at `max_tokens` is said so under it. A cancelled reply counts in `/usage`.
- An error names the provider and carries the server's whole explanation, and says when to try
  again where the server said; the lore pass waits out a rate limit and a busy engine.
- The web page stays live while a model loads, and with stream smoothing off. Loading a model
  can be cut short; typing cuts the lore pass and the warm-up at once.
- A provider that does not answer says why in both pickers. A model Ollama serves from ollama.com
  counts as remote.
- The web API renames its fields after the backend: `engines` is `providers`, a card's `name` is
  `id`, `can_load_unload` is `can_manage`, `context` is `max_context_catalogue` on a model and
  `max_context` on the session, `engine` is `provider`, the play event `thinking` is `reasoning`,
  `/api/alive` is `/api/status`, and the page's `.otk-thinking` class is `.otk-reasoning`.
- A new config.toml spells `[web] host` as `localhost`.

### Fixed

- `/set think` had no effect on llama.cpp and omlx beyond on and off, and `none` did not switch
  thinking off on every model.
- A reply the engine cut short mid-stream was filed as complete.
- With smoothing on, a cancel during the prefill did not reach the engine, and a Ctrl+C mid-reply
  was not recorded.
- `OLLAMA_HOST` is read the way Ollama reads it: scheme, port, path, a bare IPv6 address.
- The picker offered embedding, reranker and audio models, and KoboldCpp's `inactive`.
- A local engine's url typed without `/v1` failed every turn.
- A url with a letter in its port, a url a proxy redirects, and a key with a character a header
  cannot carry each ended in a traceback or a wrong failure.
- A launch that sealed an API key left the plain key in the dated backup it wrote.

## [0.4.3] - 2026-09-08

**TL;DR**

Bug fixes.

### Fixed

- `/set think none` did not stop a thinking model on llama.cpp: its server ignores
  `reasoning_effort`, the one knob otaku sent, and reads only the chat template's flag — which
  the Generic OpenAI provider never sent, and the llama.cpp provider, sending nothing, left at
  the model's own default, where Gemma 4 thinks when it likes. The local engines and the generic
  provider now send both knobs with every think setting, so "none" lands on whichever one the
  server obeys, and llama.cpp takes the levels too.
- The terminal's model picker could go down with an IndexError while it painted, when a
  provider's answer landed between the list being drawn and its cursor row being asked for —
  the row was computed on the fresh list and pointed past the text. The row is now computed on
  the same rows as the text, and a cursor a refresh left past the list lands on its last row.
- In the web UI's dossier, picking a scene or a character in the index beside the messages,
  scenes and cast tabs scrolled that index back to its top, losing the reader's place in a long
  one, and a tab switched away from and back opened on its first row rather than the one the
  reader had picked. The tabs are now built once per opening and stand as they are left: the
  index keeps its scroll, the pick stays, and an editor left open is still open on return.

## [0.4.2] - 2026-09-06

**TL;DR**

Bug fixes.

**Full version:**


### Changed

- In the web UI's settings, the context limit and a model's parameters are saved when their
  field is left — the next field, another control, a click elsewhere — as they already were on
  Enter; Esc is the one way out that reverts.
- In the web UI, Ctrl+R and Ctrl+U with the focus outside the prompt no longer reload the page
  or open its source. They are the prompt's regenerate and undo keys, and a reader who reached
  for one from a button got the browser's answer instead.
- The web UI opens with the prompt focused, so the first keystroke is the first word, and the
  focus comes back to it after Send, Regen and Undo. Not at opening on a phone, where a focused
  box would raise the keyboard unasked.

### Fixed

- In the web UI's provider panel, a URL could not be cleared and a key could not be forgotten:
  an emptied field only switched Save off. Emptying the URL, or Delete in the key field, now
  marks the change and Save applies it, file and session both — the terminal's Del, one save
  later. Test connection is gone from the panel for now: a save already asks the provider and
  redraws the row with its answer, so the button tested nothing Save had not, and pressed with
  changes pending it threw them away.
- In the web UI, the contents toggle that appears in the spine once the rail folds away read
  top to bottom, against the name beside it, and in a smaller type; it now reads the same way
  and in the same type as the name.
- On a phone, where the spine becomes a bar across the top, the theme switch still stood
  upright; it now lies along the bar, sun left and moon right, the knob sliding across.
- The theme switch did nothing on a system set to dark: off meant "follow the OS", which was
  dark, so both positions gave the same page while the knob sat at light beside a dark one. The
  knob now shows the theme in force, a click pins the other one in that browser, and the OS is
  followed only until that first click.
- A story started and left with nothing played could not be entered again: on the page the
  browser's Continue was off, and in the terminal Enter opened a dossier with nothing to pick.
  Both now resume such a story by its id alone, and the landing line says nothing is played yet.

## [0.4.1] - 2026-09-03

**TL;DR**

- A Generic OpenAI provider - any OpenAI-compatible server should work with it.
- The web UI gets a dark theme switch.

**Full version:**

### Added

- A Generic OpenAI provider, first in the provider panel (the `[generic]` section): any
  OpenAI-compatible server, by URL and key, over the protocol alone. Models are listed and
  turns streamed; a context window is read when the listing carries one. What needs an
  engine's own API is absent — no load state, no sizes, no prompt warm-up (the URL could name
  a hosted catalog, where a warm-up bills a whole window for one token).
- A theme switch in the web UI, at the spine's foot: sun, moon and the knob between them. On,
  the page is dark whatever the OS says; off, it follows the OS as before. The choice is kept in
  the browser's storage, so each browser remembers its own, and applied before the first paint.

### Changed

- In the terminal's provider panel, Delete clears the field under the cursor whichever it is —
  the URL as well as the API key.

### Fixed

- The web UI's dark theme gave an open field no ground of its own, so a field being typed in
  went near-white on a dark page.
- An unloaded Ollama model reported the model card's trained maximum as its context window,
  and the figure was kept for the session — so a story on a model that loads at Ollama's
  default window (4K on most machines) was budgeted at up to 128K and cut by the engine from
  the front, the opening first. An unloaded model now has no window until it loads; the live
  one is read once it does.
- The model picker's cursor drifted off the story's model when a provider listed earlier in
  the panel answered later, its rows landing above the cursor. The cursor now follows the
  model.

## [0.4.0] - 2026-09-02

**TL;DR**

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

**Full version:**

### Added
- `otaku web` — a web interface for the otaku on this machine. It serves one open session at the
  address `configs/config.toml`'s new `[web]` section names (loopback and port 9600 by default, so
  reaching it from another machine is an edit somebody made on purpose), and everything the
  terminal does is there: the story plays in a transcript, replies stream, and everything the
  terminal answers as a command is a button or a screen.
- `/web` serves the running session to a browser without leaving the terminal: it prints the
  address and how to stop, and Ctrl+C hands the session straight back to the prompt you left —
  nothing redrawn, because nothing started over. Nothing is served in parallel and nothing is
  copied: it is one session, played through whichever frontend you are in front of. It exists
  because `otaku web` is a thing you have to already know about, where a command is in the menu
  that opens when you type a slash, and under a heading of its own in `/help`.
- Windows is a supported platform, with an installer of its own: `install.ps1`, run as
  `powershell -ExecutionPolicy Bypass -c "irm https://otaku.sh/install.ps1 | iex"`. It is
  `install.sh` in PowerShell and makes the same promises — it fetches uv, installs otaku with it,
  never asks for administrator, and reports an otaku that uv, pipx, Scoop or Chocolatey already
  owns instead of installing a second one alongside. The one thing it edits that is yours is the
  user PATH, and only when uv's directory is missing from it. 64-bit only: on an ARM64 machine
  otaku is installed on an emulated x64 CPython, because `cryptography` publishes no ARM64 Windows
  wheel, and 32-bit Windows is refused with the reason rather than a failing build.
- New `max_context` setting (`[context]` in config.toml, 0 by default — the model's whole window —
  or any number of tokens) caps the prompt whatever the model advertises: the effective context for
  roleplay falls far short of the claimed one. Also settable as `/set max_context`
  — in the web settings panel too — which edits that one config.toml line surgically, the
  pre-edit file backed up. When the story outgrows the limit, the oldest scene summaries fold
  into the story-so-far; when even that is not enough, the verbatim tail steps down (never below
  50 messages); a story that cannot fit even then is declined with directions instead of silently
  trimmed. The context preview shows each stage: the story-so-far as its own cell before the
  scene summaries, and a tail aiming below the configured count says the window forced it.
- `/set notification on|off` — off by default — plays a sound when a reply lands, for when you look
  away mid-generation. Which sound is `configs/config.toml`'s `notification_sound`: `"default"` is
  the platform's own (Glass on macOS, the freedesktop theme's on Linux, Ding on Windows), or name a
  file of your own — WAV on Windows, which plays it through `winsound` and reads nothing else. A
  machine with no player, or a path that isn't there, rings the terminal bell instead — and what a
  bell means is your terminal's business, which is where a notification belongs.
- Prompt caching on OpenRouter, on by default: requests carry cache breakpoints, so each reply
  re-reads the story's stable prefix at the provider's cache rate instead of full input price —
  play against the hosted models for a fraction of the tokens. `prompt_cache` in
  `configs/providers.toml` decides per provider (`off`, `5m`, or `1h` for slow-paced play, since a
  cache outlived between turns is written again instead of read) — an existing `[openrouter]`
  section gains the line on the first launch after upgrading, so the setting is there to see and
  edit. The verbose stats line shows `cached N tok` per reply, `/usage` grows a CACHED column, and
  `/info` reports the setting.
- `[terminal]`'s new `theme` setting decides which shades otaku paints in: `"light"` or `"dark"`
  settles it, and `"auto"` — the default — asks the terminal as before. What changed underneath is
  the answer when the terminal will not say: it used to read as light, and now reads as dark, which
  is the likelier background and the only outcome Windows can reach, since there is nothing to ask
  there. A config from an earlier version gains the line at the head of that section on the first
  launch after upgrading, so the setting is there to see and edit.
- `/roll` plays a dice roll: `/roll 1d20+5 I search the alcove` rolls REAL dice — the OS's
  entropy, never the model's, because a model asked to roll picks dramatic numbers and bends
  them mid-narration — shows what fell, and sends the roll and your action as one message,
  framed so the model narrates exactly that outcome. `NdS` terms and flat modifiers chain with
  + and - (`2d6+3`), and `kh`/`kl` keep the highest or lowest of the dice — `2d20kh1` is
  advantage, `kl1` disadvantage. The numbers freeze into the turn as it records: `/regen`
  re-tells the same roll, never re-rolls it, and editing the played line later does not either.
  The framing is `roll_framing` in prompts.toml, editable like every prompt otaku sends; on the
  web the composer's prefix menu offers `/roll` beside `/me` and `/you`.
- The terminal's `/stories` browser drills into a four-tab dossier — Premise, Messages, Scenes,
  Cast — for any story, not only the open one, and everything editable there edits: the premise
  is written in place (a story that is not open included), a message corrects where it is read,
  and the scenes and cast are the lore browser's two lenses, now two tabs of the same window.
  ←/→ cycle the tabs from anywhere outside an edit (Tab and Shift+Tab work too), each tab keeping
  its own cursor and filter; `/lore` and `/cast` open the same dossier directly on the open
  story — on its scenes and cast tabs — and no longer refuse a story whose memory is still empty,
  since
  the premise and the messages are one Tab away. Resuming is untouched: Enter on the last message
  resumes, an earlier one still asks fork / truncate / cancel. Each scene's detail also gains a
  read-only history row — the story so far through that scene, which the web page already showed
  (as "the arc through here") and the terminal never did.
- A second sample story is seeded on a first launch beside the short one: *The Vermilion Tour*, a
  318-message ensemble play with its memory fully extracted — 15 scenes, a cast of dozens with
  journals and histories — so the story browser, the dossier and the context preview have a story
  long enough to show what they do before you have played one that size yourself. Every sample
  story ships inside the package and all of them are imported; the session still lands in the
  short one.

### Changed
- The codebase is restructured around a frontend-agnostic core. Everything that is not the
  terminal — the session, the story and lore operations, the import and export formats, the
  command surface — lives in a backend package that a frontend calls, and the terminal owns only
  the medium: what a message looks like, what a key does, what the screen holds. The layout is
  held by a test, so an import that would cross a layer fails the suite rather than the review.
  Nothing about running otaku changes: the same commands, the same state dir, the same database.
- `/balance` names each provider the way the model picker does — "OpenRouter", not the `openrouter`
  section key. A section you named yourself keeps your name, with the engine in brackets, because
  two sections of one kind are two accounts and a balance report has to tell them apart.
- config.toml's `[ui]` section is now `[terminal]`, which is what it always held: one frontend's
  looks, beside the `[web]` section that arrived this version. Your file is renamed in place on
  the first launch after upgrading — the header line and nothing else, so every value and comment
  under it stays exactly as you left it.
- The export document records the story so far through EVERY scene, not only the newest — an
  imported story's dossier reads whole, scene by scene, instead of one arc and a column of
  blanks. This is export format 3: every older document still imports exactly as before, its one
  story-so-far landing on the newest scene, where it always lived — and an older otaku refuses a
  format-3 file with directions rather than folding the new field into a summary by accident.
- The lore templates in prompts.toml are retold in three ways, and a file still holding the
  shipped wording follows on the first launch after upgrading (an edited template stays yours, as
  always). The two rollup templates say whose history each is: `story_so_far_prompt` is now
  `scene_history_prompt` and `history_prompt` is `journal_history_prompt` — your values ride the
  rename untouched. A character's history rollup now keeps the journal's first-person voice
  instead of retelling their own memory about them in third person. And the language rule in all
  three stops spelling "do not answer in English": run without thinking, as extraction is, a model
  could read that negation as the command and answer an English story in Dutch. The extract
  template also pins the reply's shape — one flat JSON object, never lore nested inside the scene —
  which a reply once got wrong and cost its scene's memory. The sizes tightened with the words:
  a scene summary's 250-400 words is now a stated bound, the story-so-far caps at 200 words
  instead of "4-8 sentences" (a count a model games with hundred-word sentences), and a
  character's history says at most 300 — these all reach the wire, and an oversized memory
  crowds out the story it exists to keep.
- `/info` no longer prints the story's premise. A premise is a document rather than a fact about
  the session — as long as the reader made it, and a lorebook imported into it filled the report
  with itself — so it is left to `/system`, which reports it on its own and at whatever length it
  is. The web's info docket never showed it, for that reason; now neither frontend does.
- Long story titles are cut at 50 characters instead of 40 — in the banner and in the line that
  names the story when it lands — with a fork's number kept whole, as before.
- Launching with a remembered model whose provider is no longer configured says so before the
  picker opens, instead of opening it without a word.
- The api key's sealing key tightens `configs/` to owner-only when it is created, as the database's
  key already did.
- One dependency fewer to install: the model picker's RAM gauge reads the machine's own numbers —
  sysconf, `/proc/meminfo` on Linux, `vm_stat` on macOS — where it used to read psutil. On macOS it
  now agrees with Activity Monitor instead of reading gigabytes rosier: app memory that has gone
  cold still counts as used, because reclaiming it means compressing or swapping it first. The
  Linux figure is unchanged — the kernel's `MemAvailable` already drew the line there.
- Requests to OpenRouter now name otaku as the app that sent them.
- The request log records each request's answer too, as its own paired line: the outcome (finished,
  cancelled, or how it failed), total and first-token seconds, token counts cached included, and
  the text that arrived — sealed exactly like the request bodies. `otaku logs requests` prints the
  answers in place and closes the day with a per-purpose summary of counts, seconds and tokens: the
  profile of where a day's model time went, read straight off the log.
- `/new` takes an optional TITLE — `/new The Long Road` names the story as it starts — and creates
  the story at once, so it is in `/stories` with its name before its first turn, where before it
  appeared only once you had played one.
- `/help` fits the screen: two columns on a wide terminal, one on a narrow one, and a description
  that wraps in its own column instead of running off the edge. The command column is spelled
  shorter here than the reference is — `/set verbose` rather than `/set verbose on|off`, `/model
  [SPEC]` with PROVIDER/MODEL moved into the description — and what a command takes is still shown
  in full by the menu as you type it.
- `/export NAME` adds `.md` when the name carries no extension of its own, since the document is
  Markdown — `/export glade` writes `glade.md`. A name with an extension keeps it.
- Enter on a menu row that takes a parameter completes the command and waits, whether the
  parameter is required or optional — before, a command whose parameter was optional ran on the
  spot, and there was no way to pick `/fork` from the menu and then name the fork. Enter sends it
  bare from there, so a command that takes nothing still runs in one press.
- The banner reads in the order a session is thought about: the story first, then the model, then
  the engine and its context window. The mark, the version and the description are unchanged, and
  `otaku web` opens with the same banner — its three lines being the address, how to open it, and
  how to stop serving.
- The mascot beside the banner is redrawn. Without colour it is now the same picture rather than a
  different one: the sprite is cut into ink and paper instead of falling back to an ASCII face, so
  a piped or `NO_COLOR` session gets the mark at the same size, in the same place.
- Spoken lines and slash commands are painted in otaku's own colours rather than the terminal's
  palette slots: they used to be "blue" and "magenta", which every terminal shades to taste, and
  they are now exact shades that come out the same everywhere. `dialogue_color` still takes a
  palette name if you would rather your own scheme decided.
- The break rule between exchanges is dimmed. At full intensity it read as a hairline on a light
  terminal and bloomed on a dark one, light-on-dark strokes carrying more weight than the same
  line does the other way round. Reduced intensity settles both, where a grey would be a guess
  against a background otaku cannot see.
- `[context] tail_messages` is now `min_tail_messages`, saying what it always meant: the tail
  never holds fewer than it — a scene ending exactly at the tail's first message stays verbatim,
  its whole span riding with the tail. Config.toml renames the key itself, the set value kept.
- The story-so-far arc now exists on every scene, and stays there. Each scene already got its arc
  as it closed; what changed is the healing: editing a summary nulls every arc composed from the
  old text — that scene's and all later ones' — and the next pass used to rebuild only the newest,
  leaving the middle scenes blank forever. It now rebuilds them all, each composed from the
  summaries up to its own scene (one rollup request per healed scene; a single-summary arc is that
  summary verbatim, no request).
- A journal's state is read-only everywhere now, like both histories: it is the extractor's own —
  superseded by the next scene's row, re-derived on every pass — so the entry is the field a hand
  corrects. The lore browser used to let the newest state be edited and refuse the older ones; the
  rule is now one sentence instead of a special case.

### Fixed
- The settings files are read and written as UTF-8, whatever the machine's locale says. They
  always held characters beyond ASCII — the comments otaku renders into config.toml alone have two
  dozen — and Python had been leaving the encoding to the platform, which is UTF-8 on macOS and
  Linux and a legacy codepage on Windows. A file otaku wrote there was not the UTF-8 that TOML
  requires, and one saved back by an editor that does write UTF-8 could no longer be read. A file
  that is not UTF-8 now says so in a sentence naming the file, rather than ending the launch with
  a traceback.
- A sealing key that cannot be read no longer ends the launch with a traceback: the provider it
  belongs to runs without its key and the launch says which one.
- The prompt warm-up after a scene closes now runs for local engines only. It exists to prefill a
  local server's cache so the next reply starts fast; on a cloud provider the same request has no
  cache to warm and was billed as a full context window for one token.

## [0.3.0] - 2026-08-17

**TL;DR**

- New feature: character card import with the `/card` command.
- Commands `/me` and `/you` suggest and autocorrect cast names.
- New commands available mid-prompt only: `/ooc` and `/cue`, both hinting the LLM — `/ooc`
  introduces a standing note, `/cue` a one-time steer that the LLM won't see later.
- The browsers (`/stories`, `/lore`, `/model`) follow the terminal theme.
- Commands get highlighted.

**Full version:**

### Added
- `/card FILE [NAME]` — import a character card (SillyTavern formats, PNG or JSON) into the
  current story. The card becomes a prompt: its fields compose through the `card_framing`
  template into one out-of-character block that rides every request verbatim — never summarized,
  never evicted — and the character speaks the card's own greeting. The cast row archives the
  card's fields as TOML, editable in `/lore`, and the wire follows the archive: the block
  composes from it at request time, so a correction reaches every later request. The archive
  rides `/export` too, so a re-imported story keeps the living card. `{{char}}` and
  `{{user}}` are recorded at import (the import asks who you play, and the story remembers the
  answer) and bind at wire time; a card's lorebook is not supported and is dropped with a note.
- The database migrates itself between schema versions: a backup is taken first, each step is
  transactional (a failure leaves the database unharmed at its version, the backup untouched),
  and a database written by a newer otaku is refused with directions instead of being guessed at.
- `/set autocorrect on|off` — whether a typed character name in `/me` and `/you` commands is
  settled to the cast's spelling.
- Two inline commands, typed inside a line rather than opening one: `/ooc` for an aside out of
  character, and `/cue` to steer just the next reply — a cue goes out with the turn it rode in on
  and is not kept in context afterwards.
- `/you NAME: HINT` — an optional hint after the name, a standing direction for how to play them,
  sent as its own `((OOC: …))` aside beside the play-as instruction whenever the turn goes out;
  `/cue` stays the one-shot form. A `you_framing` template carrying a `{body}` slot takes the hint
  into its own wording instead.
- The menu offers the story's cast where a command takes a character — each row with the
  character's description: `/me` completes `Name:` and waits for the prompt, `/you` completes the
  bare name, and `/merge` completes both sides of `A into B`.
- Commands are colored wherever they appear: while you type one, in the played block once it is
  sent, and in the story browser's rows and preview. Only real commands light up, so a slash in
  ordinary prose (`and/or`, a URL) stays prose. The completion menu marks the selected row in the
  same color, so a row reads as what it will become once inserted.

### Changed
- The extraction writes a journal for every character present in a scene — speaking, acting, or
  silently there — and entries name arrivals and departures as they happen: a journal row is now
  the story's record of presence. The refreshed template reaches existing installs too: a
  `prompts.toml` template still holding a previous release's exact text follows the new built-in,
  while an edited one is never touched.
- The lore browser's cast lists in order of appearance — the story's own order — instead of
  alphabetically.
- The system log records the app's administrative moments — the schema migration, the daily
  database backup, `otaku update` runs — besides the lore worker's actions.
- The browsers (`/stories`, `/lore`, `/model`) follow the terminal instead of painting over it:
  the pane, its text and its headings are your own colors, secondary text is dimmed rather than
  greyed, and only what has to be painted is — the selected row and a dialog floating over the
  list. They also come in a dark set now, where before every browser was light whatever the
  terminal looked like.
- Failures print in red: a provider that refused, a file that would not open, a command that
  raised. What the app merely declines to do ("Unknown command", "Nothing to regenerate") stays
  plain — an ordinary typo should not read as a fault.
- An export document declaring a newer format version than this app reads is refused with
  directions — the way a database written by a newer otaku is — instead of being parsed by
  guesswork; every older format still imports.

### Fixed
- A long `/system` premise no longer fails: deciding whether the argument named a file asked the
  filesystem a question it refuses over 255 characters, so any premise worth writing crashed the
  command instead of being stored. `/card` and `/export` asked the same question the same way.
- A command can open a multiline block — `/system """` and the lines that follow, closed with
  `"""` — where before the delimiters were stored as part of the text.
- Rewinding past a closed scene's end — a deep undo, or resuming a story from an earlier
  message — no longer kills every later extraction with a constraint failure: the abandoned
  scene stays in the tree, and the new branch closes its own scene starting at the same message.
  When a pass does crash, the failure line now names the real cause instead of blaming the
  model's reply.
- A provider edit that could not be written to `providers.toml` — a hand-broken file, a
  disk error — now says so in the panel instead of confirming a change the next launch would
  silently forget; a key that could not be forgotten on disk stays in the session too, so the
  mark never lies.
- Typed `/undo` and `/regen` erase the whole exchange again: the cursor-position query was
  triggering the blank line that separates a command's output from its typed line, quietly moving
  the cursor one row down right before the erase measured from it — so the first line of what
  should vanish stayed on screen. The shortcuts, which erase the typed line first, never armed
  that blank, which is why they were immune (#6).
- The story browser's delete now answers the key macOS captions "delete" (backspace) as well as
  the PC Del / forward-delete key it always listened for. While a filter is open, backspace still
  edits the filter.
- A line opening with `- ` reads as dash-convention dialogue — colored, the hyphen kept — where it
  used to become a `•` list bullet, which rewrote the spoken line's own mark and left it uncolored.
  Lists keep `*` and `+`.

## [0.2.2] - 2026-08-08

**TL;DR**

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

**Full version:**

Cloud arrives, and the model picker becomes the provider control center: OpenRouter and NanoGPT
next to the five local engines, API keys entered in the picker and stored sealed, providers in
their own config file — plus quality-of-life across the REPL.

### Added
- Cloud providers: OpenRouter and NanoGPT — their catalogs listed with context windows (fetched
  asynchronously, so the picker opens without waiting on the internet), the cloud prompt `$`
  instead of `>` while playing against one, and `/balance` for the account balance.
- New local backends: llama.cpp (`llama-server`) and LM Studio (load/unload included), joining
  Ollama, oMLX, and KoboldCpp; every backend now reports each model's context window, shown as a
  column in the picker.
- The picker's provider panel: every backend with its `URL:` and `API key:` fields, editable in
  place (paste works, the key never displayed), a tick for providers that answered, and models
  re-listed the moment a setting changes. Editing an unconfigured backend writes its section —
  that is how a cloud provider is added.
- `configs/providers.toml`: provider sections live in their own file now, one `[name]` section
  each — moved out of config.toml automatically, API keys sealed on the way (AES-256-GCM, the
  sealing key in the OS keychain; independent of the story encryption).
- Config migrations: one idempotent, convergent mechanism that reruns at every launch — dated
  pre-edit backups in `configs/backups/`, and a plain API key (hand-typed included) is sealed at
  the next launch.
- `otaku update`: detects how otaku was installed and runs that installer's own upgrade.
- `/undo` and `/regen` erase the taken-back turns from the screen when it is provably safe;
  `/last [N]` re-echoes the last turns for a clean view; `/clear` wipes the screen.
- A break rule wherever the played sequence on screen stops continuing: over an `/undo` report
  or a `/regen` marker that could not erase in place, and where `/new`, `/stories`, `/import`,
  or `/last` swaps or repeats the scene. It is the one thing an erase never takes, so a report
  replacing a marker slides in under the standing rule instead of stacking a second one.
- Dialogue coloring: spoken lines («quotes» and dash lines) render in blue, shaded to the
  detected terminal background (`[ui] dialogue_color`, `dialogue_bold`); the echoed prompt's band
  follows the background the same way.
- `/system` accepts an existing file's path and reads the prompt from it.
- Path autocompletion behind `@` in file arguments: the menu pops as typed and filters.
- Live smokes for all seven providers (`scenarios/live/`, `scripts/live-providers.sh`).

### Changed
- The picker lists bare model names grouped under provider captions, sizes and context flushed
  right; `/usage` prints purpose, provider, and model as columns; the banner shows the bare model
  name; `/info` reads its rows from the provider listing.
- `/regen` re-runs the last prompt when no reply stands — a failed request leaves the prompt
  unanswered, and regenerating sends it again.
- `/import` lands as deep in the scene as every other way into a story, and `/last` names what
  it put on screen.

### Fixed
- Forking carries the story's memory. A story shorter than `settle_messages` (20 by default) —
  the shipped sample among them — forked with no scenes and no journals at all.

## [0.2.1] - 2026-08-01

Packaging only — no functional changes.

### Changed
- The required Python version is now 3.11, down from 3.14.
- Dependency bounds updated.

## [0.2.0] - 2026-08-01

0.2 is a ground-up rewrite as a **roleplay terminal client**: chats are stories that can be
branched from any message, a background pass extracts lore from played messages — scenes,
characters, journals — and the context sent to the model keeps the opening and the recent tail
verbatim with scene summaries in between.

What carries over from 0.1: local models, one terminal, encrypted storage. The 0.1 chat and fleet
features (one-shot/pipe mode, `otaku list`/`otaku stop`, cross-provider RAM management,
`/remember` defaults) are gone. The database schema is new.

### Added
- Stories: branch from any message, fork with its memory, resume where you left off; a
  full-screen story browser with message-level resume and in-place editing.
- The lore engine: idle-debounced background extraction closes scenes over played messages, keeps
  per-character journals with rolled-up histories, and feeds the story back to the model as a
  recap; `/lore` browses and edits the memory in place.
- Roleplay commands: `/me`, `/you`, `/ooc` — framing joined at wire time, bodies stored verbatim.
- Import and export: a lossless Markdown story document, SillyTavern `.jsonl` chats, and
  plain-text dismantling — the format detected from the file.
- A sample story seeded into a fresh database, so a first launch lands mid-story — with or
  without a reachable model.
- Day-rotated request, system, and error logs (`otaku logs`).

## [0.1.1] - 2026-07-06

Initial public release. (`0.1.0` was a premature PyPI upload from a pre-release tree — it was
never tagged and is superseded by `0.1.1`.)

### Added
- Multi-backend client for Ollama, LM Studio, oMLX, and any OpenAI-compatible server — from one
  terminal command.
- Zero-config first run: the initial `~/.otaku/config.toml` auto-detects each built-in engine's
  port (and omlx's API key) from your environment (`OLLAMA_HOST`) or the engine's own settings
  file, falling back to the standard default. Runs once, at that first write; edit the sections
  freely afterwards.
- Cross-provider model management: `otaku list` (with `--running` to show only loaded models),
  load and unload from the picker, `otaku stop --all`, with a live RAM gauge. Provider queries run
  concurrently with a short (0.5s) probe timeout, so one configured-but-down provider no longer
  slows every command; when nothing is reachable, otaku names each provider, whether it answered,
  and points at `~/.otaku/config.toml` to fix.
- Chat REPL: streaming responses, thinking-effort control, tok/s stats (`/set verbose`, off by
  default), triple-quoted multiline input, in-chat model switching (`/model`), `/new` (fresh
  conversation) vs `/clear` (reset context in place), and slash commands.
- Streaming markdown rendering: headers, lists, blockquotes, rules, and fenced code blocks
  (syntax-highlighted via Pygments) on top of inline emphasis/code.
- omlx output smoothing (`[providers.omlx].smooth`, default on): de-jitters omlx's bursty token
  delivery into steady typing, without affecting tok/s.
- Persistent session defaults: a `[defaults]` config section (system, think, parameters,
  no_record) plus per-model overrides keyed by bare model name; `/remember` saves the current
  settings as the model's defaults.
- One-shot / pipe mode: `otaku <model> "prompt"` and `… | otaku <model>` print a plain reply and
  exit (prompt + stdin combined instruction-first), so otaku works as a Unix filter.
- Encrypted conversation history (AES-256-GCM), searchable across all conversations by full
  message content, with background LLM-generated summaries (idle-debounced so they never block
  exit or reload a cold model; `[defaults].create_summaries` / `summary_idle_seconds`), user-set
  titles (`/title`, shown in the `/history` picker), and resume-from-any-turn.
- Get answers out: `/copy` (last reply or whole chat → clipboard, via the native tool or an OSC
  52 fallback) and `/save <file>` (conversation → Markdown).
- Install via `uv tool install` or Homebrew (`brew install enclavum/tap/otaku`).
- Runs on macOS, Linux, and Windows. On Windows the streaming-time Ctrl+R (cancel + regenerate)
  shortcut is disabled — it needs a POSIX terminal — but everything else works; WSL gives full
  parity.
