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

reset-db:	## recreate the development database and apply migrations
	mkdir -p data-dev
	rm -f -- data-dev/dev.db data-dev/dev.db-wal data-dev/dev.db-shm
	DATABASE_URL=sqlite:///./data-dev/dev.db uv run alembic upgrade head

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "%-10s %s\n", $$1, $$2}'
