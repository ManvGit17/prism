# Contributing to PRism

This page is for anyone who wants to change PRism's code, tests or docs. It covers the
development environment, the checks CI runs, the code style, and how to add a setting or a
design decision record.

## Set up a development environment

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and, for anything that touches the
graph, the vector store or the queue, Docker.

```bash
git clone https://github.com/ManvGit17/prism.git
cd prism
cp .env.example .env
make sync        # uv sync: the core dependencies and the dev group
```

Optional dependency groups are installed on demand:

| Group | Adds | Install |
|---|---|---|
| `sast` | Bandit and Semgrep | `uv sync --group sast` |
| `mcp` | the MCP server | `uv sync --group mcp` |
| `app` | the GitHub App (FastAPI, Celery, PyJWT) | `uv sync --group app` |
| `observability` | the Langfuse client | `uv sync --group observability` |
| `frontier` | the Anthropic API verifier backend | `uv sync --group frontier` |
| `training` | torch and friends, for gate training | `uv sync --group training` |

Most of the unit tests need none of the services. To run the full stack:

- Start Postgres (pgvector), Neo4j and Redis with `make up`.
- Run Ollama on the host. `qwen2.5-coder:3b` works as the reviewer and `nomic-embed-text` is
  needed for vector context.
- For traces, prompts and experiments, start the self-hosted Langfuse with `make langfuse-up`
  (UI on http://localhost:3001).

The dashboard under `dashboard/` is a separate Node project. Run `npm ci` and `npm run build`
inside that folder, as CI does.

## Make targets

| Target | What it does |
|---|---|
| `make sync` | `uv sync` |
| `make up` / `make down` | Start or stop Postgres, Neo4j and Redis |
| `make langfuse-up` / `make langfuse-down` | Start or stop the self-hosted Langfuse |
| `make ps` / `make logs` / `make stats` | Container status, recent logs, memory use |
| `make test` | The whole suite, including the smoke test. Needs `make up` and the host Ollama |
| `make unit` | What CI runs: no services and no model downloads |
| `make train-test` | Gate training smoke tests on CPU with a tiny model (installs the `training` group) |
| `make lint` | `ruff check` and `ruff format --check` |
| `make format` | `ruff check --fix` and `ruff format` |
| `make typecheck` | `mypy` |
| `make check` | `lint`, `typecheck` and `unit`: run this before you push |
| `make eval-bugbench` | The whole BugBench evaluation into `eval/reports/bugbench.md` |
| `make docs-reference` | Regenerate `docs/reference/configuration.md` and `docs/reference/cli.md` from the code |
| `make docs` | Serve the documentation site locally |

## Tests and markers

Tests live in `tests/` and run with pytest. Three markers, declared in `pyproject.toml`,
separate the tests that need something extra:

| Marker | Needs | Run by `make unit` |
|---|---|---|
| `smoke` | the Compose services and the host Ollama | no |
| `download` | downloads a model on first run (about 90 MB) | no |
| `training` | the `training` dependency group (torch) | no |

`make unit` is equivalent to `uv run pytest -m "not smoke and not download and not training"`.
To run one file or one test, pass it to pytest: `uv run pytest tests/test_schemas.py -k name`.

Tests use fakes instead of live services where they can (see `tests/review_fakes.py`). A test
that needs a service or a download must carry the matching marker, or CI will fail.

## Lint, format and type checks

- **Ruff** checks the rules `E`, `F`, `I`, `UP`, `B`, `SIM` and `RUF` with a line length of 100 and
  formats the code. `make format` fixes what it can.
- **mypy** runs in strict mode with the pydantic plugin over `prism`, `tests` and `training`.
- CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`, `mypy` and the unit
  tests, and builds the dashboard.

## Code style

- Python 3.12 syntax: `X | None`, built-in generics, `StrEnum`.
- Modules start with a docstring that says what the module is for and, where a design decision
  explains it, cites the ADR (for example `(ADR 0013)`).
- Comments say why, not what.
- Data contracts are pydantic models that are frozen and forbid unknown fields
  (see `prism/schemas.py`). Validate model output against them.
- Everything is fully typed. Add a targeted `ignore_missing_imports` override in `pyproject.toml`
  only for an optional third-party package that CI does not install.
- The review service never prints; it reports through a callback and raises `ReviewError`. Print
  only in the CLI layer.
- Secrets, keys and tokens never go in the repository. `.env` is git-ignored, and
  `.env.example` holds local development defaults only.

## Add a setting

Settings live in the `Settings` class in `prism/config.py` and are read from the environment or
`.env` with the `PRISM_` prefix.

1. Add the field to `Settings` with a type and a default. Constrain it where you can
   (`Field(ge=0.0, le=1.0)`, `Literal[...]`). Use `SecretStr` for secrets.
2. Put a comment on the line above the field. The generator uses it as the description in the
   reference.
3. If most users are likely to change it, add it to `.env.example`.
4. Regenerate the reference and commit the result:

    ```bash
    make docs-reference
    ```

5. If the setting changes behaviour in a way that needs explaining, update the page that covers
   that part, or add an ADR.

## Add an ADR

Design decisions are recorded in `docs/adr/`.

1. Take the next number and name the file `NNNN-short-title.md`, with four digits and lowercase
   words joined by hyphens (the last one is `0026-threshold-sweep.md`).
2. Start with the title `# ADR NNNN: Title` and a line `Status: accepted.`
3. Use the sections `## Context`, `## Decision` and `## Consequences`. ADRs that report
   measurements add a `## Results` section between Decision and Consequences. Write in the first
   person ("I chose") and give the numbers and the file that holds them.
4. Do not rewrite an accepted ADR to change history. If a decision changes, write a new ADR that
   says which one it replaces.
5. Add a row to [`docs/adr/index.md`](docs/adr/index.md).

## Commits and pull requests

- Work on a branch and open a pull request against `main`. CI must pass.
- Write the commit subject as a short imperative sentence that says what changed and, if useful,
  why ("Fix a test's type annotation so mypy passes in CI").
- Keep a pull request to one change. Say in the description what it does, how you checked it,
  and anything you could not check.
- Add or update tests with the code. Update the docs and the ADRs a change touches, and
  regenerate the reference pages if you changed a setting or a command.
- Do not include secrets, private keys, model weights (`*.gguf`, `*.onnx`, `*.safetensors`) or
  datasets; they are git-ignored for a reason.

## Run the documentation site

The documentation is an MkDocs site under `docs/`.

```bash
make docs
```

This serves the site locally with live reload. The pages under `docs/reference/` are generated:
edit the code (or `scripts/gen_reference.py`) and run `make docs-reference` instead of editing
them by hand.
