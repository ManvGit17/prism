# Changelog

All notable changes to PRism are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0]

First release.

### Indexing and retrieval

- Parsing of Python and Java (tree-sitter) into a code graph of files, classes and functions with
  `DEFINES`, `IMPORTS`, `CALLS`, `TESTS`, `INHERITS` and `OVERRIDES` edges, and call resolution
  with confidence bands (ADR 0004).
- Function embeddings with `nomic-embed-text` stored in pgvector, searched exactly (ADR 0005).
- A base graph that follows the default branch, per-PR overlays, incremental updates and an
  index-run ledger in Postgres (ADR 0006).
- Retrieval of the callers, callees, tests, contracts and similar code for each changed hunk,
  with reranking and token-budgeted packing (ADR 0007).
- Stores for Neo4j and pgvector (`PRISM_STORE=persistent`) and an in-memory store.
- Commands `prism index`, `prism context` and `prism graph` (`callers`, `callees`, `neighbours`,
  `tests`).

### Models

- A reviewer fine-tuned from Qwen2.5-Coder 3B on CodeReviewer comment generation, served by
  Ollama; `qwen2.5-coder:3b` works as the reviewer without it (ADRs 0003 and 0009).
- A CodeBERT gate classifier, trained on the CodeReviewer quality-estimation data and served as
  ONNX (ADR 0008).
- Training code and model cards under `training/`.

### Review pipeline

- A LangGraph pipeline: planner, a review per file, verifier and aggregator, with a hunk budget
  and rule checks (ADR 0010).
- A Claude verifier run as a batch handoff, or in one go through the Anthropic API with
  `PRISM_VERIFIER=api` (ADR 0011).
- Static analysis with Bandit, Semgrep, Ruff and PMD as evidence and as comments (ADR 0012).
- Routing of security-sensitive and large hunks to Claude, secret redaction and prompt-injection
  defences (ADR 0013).
- Verified comments carry a severity, a category and a confidence, and the review carries a
  risk verdict; the output schema is in `prism/schemas.py` (ADR 0001).
- `prism review` for a diff or a repository.

### Integrations

- An MCP server for Claude Code with the tools `review_diff`, `submit_answers`,
  `pending_reviews`, `explain_finding`, `sast_evidence`, `impacted_tests` and `review_history`
  (ADR 0016).
- A GitHub App that verifies webhook signatures, holds reviews until they are verified and posts
  a summary with inline comments (ADR 0017).
- A Celery queue on Redis for App reviews, with retries and a periodic resume of held reviews
  (ADR 0018).
- A read-only dashboard: a FastAPI API and a React page (ADR 0020).
- A Docker Compose setup for Postgres (pgvector), Neo4j and Redis, with a Prometheus and Grafana
  `ops` profile (ADR 0002).

### Observability and feedback

- Self-hosted Langfuse (Compose profile `langfuse`) with a trace per review, prompts served
  from Langfuse prompt management and `prism prompts push` (ADR 0014).
- Stored reviews in Postgres with every decision, kept or dropped, and its reason (ADR 0015).
- Comment feedback from reactions, replies and resolved threads, sent to Langfuse as
  `comment_feedback` scores and to the `prism-comment-feedback` dataset, with
  `prism app feedback-report` and `prism app feedback-export` (ADR 0019).

### Evaluation

- BugBench: 198 pull requests (139 planted bugs, 30 real bugs, 29 clean) on TinyDB, aiosqlite,
  sqlite-utils and jsoup, with `prism bench` commands and results in `eval/reports/bugbench.md`
  (ADR 0021).
- A PR-Agent baseline (ADR 0022).
- Ablations: Claude reviews every hunk, the gate off, and the whole-PR view (ADRs 0023 to 0025),
  and a confidence threshold sweep (ADR 0026).
- Publishing of benchmark runs to Langfuse with `prism bench experiments`.

### Documentation

- An MkDocs site under `docs/` with generated CLI and configuration references.
- 26 architecture decision records in `docs/adr/`.
- Notes on the datasets (`docs/datasets.md`), the indexer check and the memory budget.
- `README.md`, `CONTRIBUTING.md` and `SECURITY.md`.

[1.0.0]: https://github.com/ManvGit17/prism/releases/tag/v1.0.0
