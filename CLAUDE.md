# PRism — instructions for Claude Code

The plan is [PLAN.md](PLAN.md). Read sections 0.1 and 0.2 before planning a phase. Build order
is in section 4 (0, 1, 2, 5, a 10-PR benchmark slice, then 6, 7, 8, with 3 and 4 alongside), not
the phase numbers.

## Rules (PLAN.md section 7)

- One phase per session, starting in plan mode. Wait for approval before writing code.
- Pydantic schemas are the contract. Every LLM output is validated, with at most 2 retries, then
  dropped and logged.
- Training code must run on a **free T4 (16 GB)**. Keep notebooks thin and put the logic in
  `training/*.py`. The build machine has no NVIDIA GPU, so Unsloth and 4-bit loading do not run on
  it: locally the logic is imported and unit-tested, with a CPU smoke test on a tiny model and a
  handful of rows; the real run happens on Colab/Kaggle.
- The build machine is **Windows 11**. Anything that needs Linux (Celery workers, SAST tools where
  needed) runs in a container. Ollama runs on the host.
- Reviewer model output is `{line, comment}` only (`RawComment`). Severity and category are set by
  the verifier (`ReviewComment`).
- **No fabricated metrics.** Every number in README or reports comes from a committed eval script
  and its logged output.
- Secrets only in `.env`. GitHub App private key path comes from env and is never committed.
- Write `docs/learning-log.md` after each phase: a plain-English summary plus 5 quiz questions.

## Commands

| Command | What it does |
|---|---|
| `make sync` | Install dependencies with `uv` (Python 3.12) |
| `make up` / `make down` | Start or stop Postgres + pgvector, Neo4j, Redis |
| `make test` | All tests, including the smoke test (needs `make up` and the host Ollama) |
| `make unit` | Tests that need no services or model downloads; this is what CI runs |
| `make check` | Lint, type check, unit tests (no torch needed) |
| `make train-test` | Install the `training` group (CPU torch) and run the `training`-marked CPU smoke tests |
| `uv run --group training python -m training.gate.run --data <zip> --out <dir>` | Gate pipeline: train, threshold, ONNX export, parity (`--help` for flags); the real run is on Kaggle (`training/README.md`) |
| `make stats` | Memory per container |
| `uv run prism index <path>` | Index a repo into Neo4j + pgvector (`--no-embed`, `--store memory`) |
| `uv run prism graph callers <symbol>` | Query the graph: `callers`, `callees`, `tests`, `neighbours --hops 2`; `--path <repo>` indexes in memory first |
| `uv run prism context <repo> <diff>` | Ranked review context per hunk with token counts (`--mode`, `--budget`, `--json`, `--render`, `--store memory`) |

## Conventions

- Python 3.12, `uv`, ruff (line length 100), mypy strict, pytest.
- Settings come from `prism.config.get_settings()`; environment variables are prefixed `PRISM_`.
- Tests that need a running service carry `@pytest.mark.smoke`.
- Each design decision gets an ADR in `docs/adr/`.
- The repo is public. Private planning files and the summary PDF stay out of it.
