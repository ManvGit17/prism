.PHONY: sync up down langfuse-up langfuse-down ps logs test unit train-test lint format typecheck check stats eval-bugbench docs docs-build docs-reference

sync:
	uv sync

up:
	docker compose up -d --wait

down:
	docker compose down

# Self-hosted Langfuse for traces and prompts (ADR 0014). UI at http://localhost:3001.
langfuse-up:
	docker compose --profile langfuse up -d --wait

langfuse-down:
	docker compose --profile langfuse stop langfuse-web langfuse-worker langfuse-clickhouse langfuse-minio langfuse-redis langfuse-postgres

ps:
	docker compose ps

logs:
	docker compose logs --tail 100

# Everything, including the smoke test. Needs `make up` and the host Ollama.
test:
	uv run pytest

# What CI runs: no services and no model downloads.
unit:
	uv run pytest -m "not smoke and not download and not training"

# Gate training smoke tests on CPU with a tiny model. Installs the training group (torch, CPU build).
train-test:
	uv sync --group training
	uv run --group training pytest -m training

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

# The whole BugBench evaluation (bugbench/configs.toml) into eval/reports/bugbench.md. Set
# PRISM_BENCH_ROOT to where the clones and runs live. Stops with exit code 2 while Claude
# answers are missing and lists the handoff rounds to answer; run it again afterwards.
eval-bugbench:
	uv run --group sast prism bench all bugbench/configs.toml

# The documentation site (mkdocs.yml, docs/). Served locally only.
docs:
	uv run --group docs mkdocs serve -a 127.0.0.1:8000

docs-build:
	uv run --group docs mkdocs build --strict

# Regenerate docs/reference/configuration.md and docs/reference/cli.md from the code.
docs-reference:
	uv run python scripts/gen_reference.py
