ENV = otaku2

.PHONY: lint format typecheck

lint:
	conda run -n $(ENV) ruff check otaku/

format:
	conda run -n $(ENV) ruff format otaku/

typecheck:
	conda run -n $(ENV) mypy
