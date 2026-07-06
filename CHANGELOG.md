# Changelog

All notable changes to otaku are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and otaku follows
[Semantic Versioning](https://semver.org/) — while pre-1.0, minor releases may
include breaking changes.

## [Unreleased]

## [0.1.1] - 2026-07-06

Initial public release. (`0.1.0` was a premature PyPI upload from a pre-release
tree — it was never tagged and is superseded by `0.1.1`.)

### Added
- Multi-backend client for Ollama, LM Studio, MLX (omlx), and any
  OpenAI-compatible server — from one terminal command.
- Zero-config first run: the initial `~/.otaku/config.toml` auto-detects each
  built-in engine's port (and omlx's API key) from your environment
  (`OLLAMA_HOST`) or the engine's own settings file, falling back to the
  standard default. Runs once, at that first write; edit the sections freely
  afterwards.
- Cross-provider model management: `otaku list` (with `--running` to show only
  loaded models), load and unload from the picker, `otaku stop --all`, with a
  live RAM gauge. Provider queries run concurrently with a short (0.5s) probe
  timeout, so one configured-but-down provider no longer slows every command;
  when nothing is reachable, otaku names each provider, whether it answered,
  and points at `~/.otaku/config.toml` to fix.
- Chat REPL: streaming responses, thinking-effort control, tok/s stats
  (`/set verbose`, off by default), triple-quoted multiline input, in-chat
  model switching (`/model`), `/new` (fresh conversation) vs `/clear` (reset
  context in place), and slash commands.
- Streaming markdown rendering: headers, lists, blockquotes, rules, and fenced
  code blocks (syntax-highlighted via Pygments) on top of inline emphasis/code.
- omlx output smoothing (`[providers.omlx].smooth`, default on): de-jitters
  omlx's bursty token delivery into steady typing, without affecting tok/s.
- Persistent session defaults: a `[defaults]` config section (system, think,
  parameters, no_record) plus per-model overrides keyed by bare model name;
  `/remember` saves the current settings as the model's defaults.
- One-shot / pipe mode: `otaku <model> "prompt"` and `… | otaku <model>`
  print a plain reply and exit (prompt + stdin combined instruction-first),
  so otaku works as a Unix filter.
- Encrypted conversation history (AES-256-GCM), searchable across all
  conversations by full message content, with background LLM-generated
  summaries (idle-debounced so they never block exit or reload a cold model;
  `[defaults].create_summaries` / `summary_idle_seconds`), user-set titles
  (`/title`, shown in the `/history` picker), and resume-from-any-turn.
- Get answers out: `/copy` (last reply or whole chat → clipboard, via the native
  tool or an OSC 52 fallback) and `/save <file>` (conversation → Markdown).
- Install via `uv tool install` or Homebrew (`brew install enclavum/tap/otaku`).
- Runs on macOS, Linux, and Windows. On Windows the streaming-time Ctrl+R
  (cancel + regenerate) shortcut is disabled — it needs a POSIX terminal — but
  everything else works; WSL gives full parity.
