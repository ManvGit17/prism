# PRism

A repo-aware AI code reviewer. It parses a repository into a code knowledge graph, retrieves
callers, callees and tests for each changed function, and reviews pull-request hunks with a small
local model whose comments a verifier then checks.

**Status:** Phase 1 done (code indexer and knowledge graph for Python and Java). Nothing reviews
code yet. The plan is in [PLAN.md](PLAN.md).

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

## Index a repo

```bash
uv run prism index path/to/repo                # Neo4j + pgvector; add --no-embed to skip embeddings
uv run prism graph callers Cart.add            # also: callees, tests, neighbours --hops 2
uv run prism graph callers Cart.add --path path/to/repo   # index in memory first, no services
```

Python and Java are supported. With more than one repo indexed, pass `--repo-id`.
