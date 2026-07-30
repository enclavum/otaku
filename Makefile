# RUN prefixes every command (override: `make lint RUN=` with an
# activated venv, or point it at any other environment runner).
RUN ?= uv run

.PHONY: lint format typecheck test scenarios

lint:
	$(RUN) ruff check otaku/ tests/ scenarios/

format:
	$(RUN) ruff format otaku/ tests/ scenarios/

typecheck:
	$(RUN) mypy

test:
	$(RUN) pytest

scenarios:
	$(RUN) pytest scenarios/
