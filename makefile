.DEFAULT_GOAL := help

dev:      ## run web with reload
	uv run uvicorn app.main:app --reload --port 8000

worker:   ## one poll cycle (real worker arrives in Issue 7)
	uv run python -m app.worker --once

test:	  ## run pytest
	uv run pytest

lint:	  ## use ruff to check and format code
	uv run ruff check . && uv run ruff format --check .

fmt:	  ## use ruff to format code; automatically fixes issues
	uv run ruff format . && uv run ruff check --fix .

check: lint test	## run lint and test

## will add targrets for deploy, logs, backup, and reset-db in the future

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "%-10s %s\n", $$1, $$2}'
