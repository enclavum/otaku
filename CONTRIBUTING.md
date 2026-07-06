# Contributing to otaku

otaku is a small, focused project — a terminal chat client for local LLMs.
Contributions that keep it sharp are very welcome.

## Development setup

otaku uses [uv](https://docs.astral.sh/uv/). With the repo cloned:

```bash
uv sync --extra dev          # create a venv and install runtime + dev deps
# or, with pip:
pip install -e ".[dev]"
```

Python 3.11+ is required.

## Before opening a PR

The automated safety net is **type-checking + linting**, run on every commit via
pre-commit:

```bash
pre-commit install           # one-time: wires ruff + mypy into git hooks
```

You can run the same checks by hand — or all of them at once with `make check`:

```bash
make check                   # lint + format-check + mypy + tests (see `make help`)

# …or individually:
ruff check otaku tests
ruff format --check otaku tests
mypy otaku
pytest
```

The `Makefile` targets run inside the conda env by default; override with
`make <target> RUN=` (bare PATH) or `RUN="uv run"`.

### On tests

otaku ships a **full pytest suite** (`tests/`) covering every module — unit
tests plus CLI tests that drive the typer app end-to-end via
`typer.testing.CliRunner`. Alongside strict `mypy` + `ruff`, it's the automated
net; behavioural changes should still be smoke-tested against a real backend
(Ollama, LM Studio, or an MLX server).

```bash
pytest                       # whole suite (fast — no network, no real disk)
pytest tests/test_crypto.py  # one module
```

Conventions for new tests:

- An autouse `_isolate` fixture (in `tests/conftest.py`) redirects every
  `~/.otaku` path into a tmp dir, points the DB at a throwaway sqlite file,
  swaps the OS keychain for an in-memory store, and clears the client cache.
  **No test may touch the real home directory, keychain, or network.**
- HTTP is mocked with [`respx`](https://lundberg.github.io/respx/) (the provider
  layer speaks `httpx`); never hit a live server in a test.
- Shared, non-fixture helpers live in `tests/support.py` (`make_provider`, the
  SSE builders, `FakeClient`); import them with `from tests.support import ...`.
- The TUI pickers are tested by calling their behaviour methods directly — the
  prompt_toolkit `Application` is never run.
- When you add a provider, slash command, or KEK provider, add tests for it in
  the same change (see the "Common tasks" checklists in `CLAUDE.md`).

## Style

- Ruff owns formatting and lint (line length 100) — let it do the work.
- Keep dependencies minimal: otaku aims to install fast and run anywhere a
  terminal does. Prefer the standard library; a new runtime dependency needs a
  real justification.
- Match the surrounding code: full type hints, `from __future__ import
  annotations`, small focused modules.

## Reporting bugs and requesting features

Open an issue describing what you ran, what you expected, and what happened —
including your provider (Ollama / LM Studio / MLX) and the model. For security
issues, see [SECURITY.md](SECURITY.md) instead of the public tracker.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
