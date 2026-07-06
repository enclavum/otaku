# otaku — developer task runner.
#
# Commands run inside the project's conda env by default (see CLAUDE.md).
# Override the runner for other setups, e.g.:
#     make test RUN=            # use whatever python is on PATH
#     make test RUN="uv run"    # use uv
#
# Pass extra arguments with ARGS=..., e.g.:
#     make test ARGS="tests/test_crypto.py -x"
#     make run  ARGS="ollama/llama3"

RUN ?= conda run -n otaku
SRC := otaku tests
ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help install lint format format-check fix typecheck test check run hooks clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

install: ## Install the package + dev dependencies (editable)
	$(RUN) pip install -e ".[dev]"

lint: ## Lint with ruff
	$(RUN) ruff check $(SRC)

format: ## Auto-format with ruff
	$(RUN) ruff format $(SRC)

format-check: ## Check formatting without writing changes
	$(RUN) ruff format --check $(SRC)

fix: ## Auto-fix lint issues, then format
	$(RUN) ruff check --fix $(SRC)
	$(RUN) ruff format $(SRC)

typecheck: ## Type-check with mypy (strict; otaku/ only)
	$(RUN) mypy otaku

test: ## Run the test suite (extra args via ARGS=...)
	$(RUN) python -m pytest $(ARGS)

check: lint format-check typecheck test ## Full pre-flight: lint + format + types + tests

run: ## Run the app (args via ARGS=..., e.g. ARGS="ollama/llama3")
	$(RUN) otaku $(ARGS)

hooks: ## Run all pre-commit hooks against every file
	$(RUN) pre-commit run --all-files

clean: ## Remove caches and compiled artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find $(SRC) -type d -name __pycache__ -prune -exec rm -rf {} +
