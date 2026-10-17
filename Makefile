# RUN prefixes every command (override: `make lint RUN=` with an
# activated venv, or point it at any other environment runner).
RUN ?= uv run

.PHONY: lint format typecheck test scenarios regression test-model share

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

# The terminal's screen played turn by turn against its own echo
# (scenarios/cli/test_screen.py): minutes, so left out of `scenarios`.
regression:
	$(RUN) pytest scenarios/ --slow -m slow

# A fake llama.cpp on llama.cpp's own port, for looking at the transcript
# by hand: its lines' leading digits choose the blocks (scripts/test_model.py).
# PORT=… moves it, PACE=… sets the seconds between streamed pieces. It runs
# on the project's conda env's own interpreter, not through `conda run`, so
# Ctrl+C reaches it alone and it ends quietly.
TEST_MODEL_PYTHON ?= $(shell conda run -n otaku python -c 'import sys; print(sys.executable)')
test-model:
	@$(TEST_MODEL_PYTHON) scripts/test_model.py $(if $(PORT),--port $(PORT)) $(if $(PACE),--pace $(PACE))

# The working tree as a wheel, served on this machine's network until
# Ctrl+C, for another machine to install with the line it prints. Built
# into build/share, never dist/ (the releases'). SHARE_PORT=… moves it.
SHARE_PORT ?= 8765
share:
	@rm -rf build/share
	@uv build --wheel --out-dir build/share
	@address=$$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | cut -d' ' -f1); \
	test -n "$$address" || { echo "No network address found."; exit 1; }; \
	wheel=$$(basename build/share/*.whl); \
	echo; echo "  uv tool install --reinstall \"otaku @ http://$$address:$(SHARE_PORT)/$$wheel\""; echo; \
	python3 -m http.server $(SHARE_PORT) --bind $$address --directory build/share
