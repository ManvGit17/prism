# PRism

A repo-aware AI code reviewer. It parses a repository into a code knowledge graph, retrieves
callers, callees and tests for each changed function, and reviews pull-request hunks with a small
local model whose comments a verifier then checks.

**Status:** Phase 0 (scaffolding). Nothing reviews code yet. The plan is in [PLAN.md](PLAN.md).

## Run the services

Needs Docker, [`uv`](https://docs.astral.sh/uv/), `make`, and [Ollama](https://ollama.com) on
the host.

```bash
ollama pull qwen2.5-coder:3b
make sync
make up
make test
```

`make unit` runs the tests that need no services.
