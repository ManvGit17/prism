# PRism

A repo-aware AI code reviewer. It parses a repository into a code knowledge graph, retrieves
callers, callees and tests for each changed function, and reviews pull-request hunks with a small
local model whose comments a verifier then checks.

**Status:** Phases 1 to 4 done: code graph for Python and Java, context retrieval for a diff, a
gate classifier and a fine-tuned reviewer model ([results](eval/reports/sft.md)). Nothing reviews
code end to end yet. The plan is in [PLAN.md](PLAN.md).

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
