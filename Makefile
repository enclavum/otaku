# RUN prefixes every command (override: `make lint RUN=` with an
# activated venv, or point it at any other environment runner).
RUN ?= uv run

.PHONY: lint format typecheck test scenarios test-model

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

# A fake llama.cpp on llama.cpp's own port, for looking at the transcript
# by hand: its lines' leading digits choose the blocks (scripts/test_model.py).
# PORT=… moves it, PACE=… sets the seconds between streamed pieces. It runs
# on the project's conda env's own interpreter, not through `conda run`, so
# Ctrl+C reaches it alone and it ends quietly.
TEST_MODEL_PYTHON ?= $(shell conda run -n otaku python -c 'import sys; print(sys.executable)')
test-model:
	@$(TEST_MODEL_PYTHON) scripts/test_model.py $(if $(PORT),--port $(PORT)) $(if $(PACE),--pace $(PACE))
