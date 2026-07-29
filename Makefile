ENV = otaku2

.PHONY: lint format typecheck test scenarios

lint:
	conda run -n $(ENV) ruff check otaku/ tests/ scenarios/

format:
	conda run -n $(ENV) ruff format otaku/ tests/ scenarios/

typecheck:
	conda run -n $(ENV) mypy

test:
	conda run -n $(ENV) pytest

scenarios:
	conda run -n $(ENV) pytest scenarios/
