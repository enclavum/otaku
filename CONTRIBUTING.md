# Contributing to otaku

otaku is a small, focused project — a roleplay terminal client.
Contributions that keep it sharp are very welcome.

## Development setup

otaku uses [uv](https://docs.astral.sh/uv/). With the repo cloned:

```bash
uv sync                      # create a venv and install runtime + dev deps
```

A virtual environment with Python 3.11 is required.

## Before opening a PR

Run the whole safety net — lint, strict typing, unit tests, and the
scenario suite:

```bash
make lint typecheck test scenarios RUN="uv run"
```

(`RUN` prefixes every Makefile command; use `RUN=` with an already
activated venv.)

### On tests

There are two suites, with a deliberate division of labor:

- **Unit tests** (`tests/`, `make test`) exist ONLY for pure functions —
  no disk, network, database, or terminal. They are written from a
  module's documented contract, never by reading its code, and the tree
  mirrors the package: `tests/lore/test_assembler.py` covers
  `otaku/lore/assembler.py`.
- **Scenario tests** (`scenarios/`, `make scenarios`) play user stories
  against the real application: the real launch composition over a
  throwaway state dir, a scripted OpenAI-compatible server as the model,
  prompt_toolkit screens driven by real keystrokes, and a few journeys
  running the actual binary in a pty. Deterministic and offline. The
  `live`-marked smokes talk to a real local model (`ollama pull gemma3`)
  and skip themselves when it is absent; deselect with `-m "not live"`.

A behavior change comes with the scenario that proves it — red first
against the unfixed code where practical. No test may touch your real
home directory, keychain, or network.

## Style

- Ruff owns formatting and lint (line length 100) — let it do the work;
  `mypy` runs strict.
- Keep dependencies minimal: prefer the standard library; a new runtime
  dependency needs a real justification.
- Match the surrounding code: full type hints, small focused modules,
  module docstrings that say what the module owns.

## Reporting bugs and requesting features

Open an issue describing what you ran, what you expected, and what
happened — including your provider (Ollama / omlx / KoboldCpp) and
the model. For security issues, see
[SECURITY.md](SECURITY.md) instead of the public tracker.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
