# PRism

A repo-aware AI code reviewer. It parses a repository into a code knowledge graph, retrieves
callers, callees and tests for each changed function, and reviews pull-request hunks with a small
local model whose comments a verifier then checks.

**Status:** Phases 1 to 5 done: code graph for Python and Java, context retrieval for a diff, a
gate classifier, a fine-tuned reviewer model ([results](eval/reports/sft.md)) and the review
pipeline that puts them together with static analysis, a Claude verifier and tracing
([what it does on real diffs](eval/reports/review_pipeline.md)). No GitHub App or benchmark yet.
The plan is in [PLAN.md](PLAN.md).

## Run the services

Needs Docker, [`uv`](https://docs.astral.sh/uv/), `make`, and [Ollama](https://ollama.com) on
the host.

```bash
ollama pull qwen2.5-coder:3b
ollama pull nomic-embed-text
make sync
make up
make test
```

`make unit` runs the tests that need no services.

The default reviewer is the fine-tuned `prism-reviewer:v2`, built from a GGUF that is not
published yet ([model card](training/model_cards/reviewer.md)). Without it, put
`PRISM_REVIEWER_MODEL=qwen2.5-coder:3b` in `.env`; the two score the same with the judge.

## Index a repo

```bash
uv run prism index path/to/repo                # Neo4j + pgvector; add --no-embed to skip embeddings
uv run prism graph callers Cart.add            # also: callees, tests, neighbours --hops 2
uv run prism graph callers Cart.add --path path/to/repo   # index in memory first, no services
```

## Context for a diff

```bash
git -C path/to/repo diff > change.diff         # the repo is checked out at the PR head
uv run prism context path/to/repo change.diff  # ranked context per hunk with token counts
uv run prism context path/to/repo change.diff --mode graph --render   # the prompt block
```

`--mode` is `none`, `vector`, `graph` or `graph+vector`; `--budget` is tokens per hunk.

Python and Java are supported. With more than one repo indexed, pass `--repo-id`.

## Review a diff

```bash
uv sync --group sast                           # Bandit and Semgrep; PMD is optional (PRISM_PMD_HOME)
uv run prism review --repo path/to/repo --diff change.diff --store memory --mode graph
```

The local reviewer comments on each hunk the gate passes, Bandit, Semgrep, Ruff and PMD add
findings, and hunks that look security-sensitive or are large go to Claude instead. Claude then
checks every comment against the code and the retrieved context. Without an API key that is a
handoff: the first run stops with exit code 2 after writing requests and a rubric into the work
directory (`.prism-work/...`), a Claude session writes the answers next to them, and the same
command again prints the `PRReview` JSON. With `ANTHROPIC_API_KEY` set,
`PRISM_VERIFIER=api` (and `uv sync --group frontier`) does it in one run. `--verifier rules` skips
Claude and keeps only the deterministic checks. Secrets are redacted before anything reaches
Claude. How it decides is in ADRs 0010 to 0013.

Traces and prompts can go to a self-hosted Langfuse:

```bash
uv sync --group observability
make langfuse-up                               # UI at http://localhost:3001, sign up there
uv run prism prompts push --label production
PRISM_TRACING=langfuse PRISM_PROMPT_SOURCE=langfuse uv run prism review ...
```

Each review is one trace with a span per file and hunk (ADR 0014). It needs about 2 GB of
Docker memory; `make langfuse-down` stops it.
