# Architecture decision records

Each ADR records one design decision: the context, what I chose and what follows from it. All
26 are accepted. The ADRs from 0021 on report measurements on BugBench.

| No. | Title | Summary |
|---|---|---|
| 0001 | [Separate schemas for model output and verified comments](0001-raw-and-verified-comment-schemas.md) | The reviewer model may emit only `RawComment`; only the verifier creates a `ReviewComment` with severity, category and confidence. |
| 0002 | [Ollama on the host, three Compose services, ops profile](0002-host-ollama-and-compose-layout.md) | Ollama runs on the host; Compose runs Postgres with pgvector, Neo4j and Redis with memory limits, and Prometheus and Grafana sit in an `ops` profile. |
| 0003 | [Qwen2.5-Coder 3B as the base reviewer model](0003-base-reviewer-model.md) | `qwen2.5-coder:3b` is the base default, chosen over the 1.5B after a manual check on planted bugs. |
| 0004 | [Best-effort call resolution with confidence bands](0004-call-resolution.md) | Call sites are resolved by an ordered set of rules, and each CALLS edge carries a confidence so retrieval knows how far to trust it. |
| 0005 | [Function embeddings with nomic-embed-text on the host Ollama, exact search](0005-embeddings.md) | Functions are embedded with `nomic-embed-text` (768 dimensions) from the host Ollama and searched exactly in pgvector. |
| 0006 | [Base graph, PR overlays and the index-run ledger](0006-index-consistency.md) | The base graph follows the default branch, PRs are parsed into overlays, and a ledger in Postgres records index runs. |
| 0007 | [Retrieval, reranking and packing of review context](0007-retrieval-and-packing.md) | A hunk maps to a symbol, related code is retrieved from the graph and vectors, reranked, and packed in priority order into a token budget. |
| 0008 | [CodeBERT gate classifier, trained on Kaggle, served as ONNX](0008-gate-classifier.md) | A CodeBERT classifier trained on the quality-estimation data skips hunks that get no human comment, with a threshold set for about 90% recall. |
| 0009 | [Reviewer fine-tuning writes the comment only; the line is picked afterwards](0009-reviewer-fine-tuning.md) | The data has no line labels, so the model writes only the comment and `anchor_line` places it. |
| 0010 | [Review pipeline: planner, budget, rule checks, fan-out](0010-review-pipeline.md) | A LangGraph pipeline plans the PR, spends a hunk budget, reviews files in parallel and applies rule checks before verification. |
| 0011 | [The verifier is a Claude model, run as a batch handoff](0011-claude-verifier-handoff.md) | `prism review` writes verification requests, a Claude session answers them, and the same command run again finishes the review. |
| 0012 | [Static analysis as evidence and as comments](0012-sast-evidence.md) | Bandit, Semgrep, Ruff and PMD findings supply the evidence a security comment needs and can become comments themselves. |
| 0013 | [Routing to Claude, secret redaction, prompt injection](0013-routing-redaction-injection.md) | Sensitive and large hunks go to Claude, secrets are redacted before any external call, and PR text is treated as untrusted. |
| 0014 | [Self-hosted Langfuse for traces and prompts](0014-langfuse-tracing-and-prompts.md) | A Compose profile runs Langfuse locally for a trace per PR and for prompt management, so diffs stay on the machine. |
| 0015 | [Reviews are stored with every decision](0015-stored-reviews.md) | `reviews` and `review_findings` tables keep every comment considered, with whether it was posted and why. |
| 0016 | [Review tools over MCP, with the client as verifier](0016-mcp-review-tools.md) | One review service feeds the CLI, the MCP server and the App; the MCP client answers the verification requests. |
| 0017 | [The GitHub App holds reviews until Claude has verified them](0017-github-app.md) | The App checks webhook signatures, authenticates as an installation, holds unverified reviews and posts them once verified. |
| 0018 | [A Celery queue for App reviews](0018-review-queue.md) | Reviews run as Celery tasks on Redis with retries, an idempotency key and handling of superseded pushes. |
| 0019 | [Feedback on posted comments, into Langfuse](0019-comment-feedback.md) | Reactions, replies and resolved threads are stored, sent to Langfuse as scores and added to a dataset. |
| 0020 | [A small, read-only dashboard](0020-dashboard.md) | A read-only API on 127.0.0.1 and a React page show reviews, findings, feedback and the eval reports. |
| 0021 | [BugBench, real pull requests with known bugs](0021-bugbench.md) | A benchmark built from real commits of four open-source projects, with planted bugs, real bugs and clean PRs. |
| 0022 | [PR-Agent as the baseline, with Claude through a handoff](0022-pr-agent-baseline.md) | PR-Agent runs on the same pull requests, with the same Claude model behind it, as a baseline. |
| 0023 | [Ablation, Claude reviews every hunk](0023-ablation-claude-only.md) | `PRISM_ROUTE_ALL=true` sends every hunk to Claude to test how much the local reviewer matters. |
| 0024 | [Ablation, the gate off](0024-ablation-gate.md) | Running with `PRISM_GATE=pass-all` shows what the gate saves against what it costs in recall. |
| 0025 | [Ablation, the whole-PR view](0025-ablation-whole-pr.md) | `PRISM_REVIEW_UNIT=pr` puts the routed hunks of a PR into one request to test the whole-PR view. |
| 0026 | [The confidence threshold sweep](0026-threshold-sweep.md) | `prism bench sweep` re-answers a finished run at each confidence threshold from saved answers. |
