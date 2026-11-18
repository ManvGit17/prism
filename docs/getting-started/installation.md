# Installation

This page installs PRism and everything it can use. If you only want to review a diff from the
command line, the [Quickstart](quickstart.md) needs much less: no Docker at all.

## Requirements

| Need | For | Notes |
|---|---|---|
| Python 3.12 and [`uv`](https://docs.astral.sh/uv/) | everything | `uv` installs Python 3.12 for you if it is missing |
| [Ollama](https://ollama.com) on the host | the local reviewer and embeddings | listens on `127.0.0.1:11434` by default |
| Git | reviewing diffs, indexing repos | |
| Docker with Compose, and `make` | the code graph, stored reviews, the GitHub App queue, Langfuse | optional for CLI reviews |
| Node.js 20+ | building the dashboard | optional |
| Claude | verification | a Claude Code session (no key needed) or an Anthropic API key |

Memory: the core services need about 2 GB, Langfuse about 2 GB more, and the reviewer model
about 2.5 GB while it runs. See [running the services](../operations/running-the-services.md).

## 1. Get the code

```bash
git clone https://github.com/ManvGit17/prism.git
cd prism
uv sync
cp .env.example .env
```

`uv sync` installs the core. Add the optional groups you need:

| Group | Adds |
|---|---|
| `sast` | Bandit and Semgrep (Ruff is always there; PMD is a separate download, see below) |
| `mcp` | the MCP server for Claude Code |
| `app` | the GitHub App (FastAPI, Celery) and the dashboard server |
| `observability` | Langfuse tracing, prompts, feedback and experiments |
| `frontier` | the Anthropic SDK, for `PRISM_VERIFIER=api` |
| `training` | PyTorch (CPU) for the training scripts' local tests |

```bash
uv sync --group sast --group mcp --group app --group observability
```

## 2. Get the models

The reviewer runs in Ollama. The base model works out of the box:

```bash
ollama pull qwen2.5-coder:3b
ollama pull nomic-embed-text      # embeddings, for vector context
```

To use the fine-tuned reviewer and the gate instead, follow [Models](../guides/models.md). Then
set the reviewer in `.env`:

```bash
PRISM_REVIEWER_MODEL=qwen2.5-coder:3b      # or prism-reviewer:v2 once registered in Ollama
```

## 3. Start the services (optional)

```bash
make up            # Postgres with pgvector, Neo4j, Redis
make langfuse-up   # self-hosted Langfuse on http://localhost:3001 (optional)
```

`make test` runs every test against them; `make unit` runs the tests that need no services.

## 4. Check it works

```bash
make unit
uv run prism --help
```

Then review something: [Quickstart](quickstart.md).

## Optional: PMD for Java

PMD is not a Python package. Download a PMD 7 release from the
[PMD project](https://pmd.github.io), unpack it, and point PRism at it:

```bash
PRISM_PMD_HOME=/path/to/pmd-bin-7.x.y
```

Without it, Java files are still reviewed; only the PMD findings are missing.
