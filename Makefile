ENV = otaku2

.PHONY: lint format typecheck test

lint:
	conda run -n $(ENV) ruff check otaku/ tests/

format:
	conda run -n $(ENV) ruff format otaku/ tests/

typecheck:
	conda run -n $(ENV) mypy

test:
	conda run -n $(ENV) pytest
