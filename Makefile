.PHONY: sync up down ps logs test unit lint format typecheck check stats

sync:
	uv sync

up:
	docker compose up -d --wait

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs --tail 100

# Everything, including the smoke test. Needs `make up` and the host Ollama.
test:
	uv run pytest

# What CI runs: no services needed.
unit:
	uv run pytest -m "not smoke"

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

check: lint typecheck unit

stats:
	docker stats --no-stream
