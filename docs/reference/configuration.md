# Configuration reference

Every setting is read from the environment or from `.env` in the repo root, with the
`PRISM_` prefix: `review_max_hunks` is `PRISM_REVIEW_MAX_HUNKS`. `.env.example` lists the
ones you are most likely to change. This page is generated from `prism/config.py` by
`make docs-reference`.

| Variable | Type | Default | Description |
|---|---|---|---|
| `PRISM_POSTGRES_HOST` | str | `127.0.0.1` | 127.0.0.1, not localhost: Compose publishes on 127.0.0.1 only, and on Windows "localhost" tries ::1 first, which cost 2 s per Neo4j and up to 130 s per Postgres connect. |
| `PRISM_POSTGRES_PORT` | int | `5432` |  |
| `PRISM_POSTGRES_USER` | str | `prism` |  |
| `PRISM_POSTGRES_PASSWORD` | SecretStr | (secret) |  |
| `PRISM_POSTGRES_DB` | str | `prism` |  |
| `PRISM_NEO4J_URI` | str | `bolt://127.0.0.1:7687` |  |
| `PRISM_NEO4J_USER` | str | `neo4j` |  |
| `PRISM_NEO4J_PASSWORD` | SecretStr | (secret) |  |
| `PRISM_REDIS_URL` | str | `redis://127.0.0.1:6379/0` |  |
| `PRISM_OLLAMA_URL` | str | `http://127.0.0.1:11434` | Ollama runs on the host, not in Compose. |
| `PRISM_REVIEWER_MODEL` | str | `prism-reviewer:v2` | The fine-tuned reviewer (ADR 0009). It is built locally from the Kaggle GGUF; the untuned `qwen2.5-coder:3b` scores the same with the judge and works with a plain `ollama pull`. |
| `PRISM_CALL_CONFIDENCE_THRESHOLD` | float | `0.5` | Graph queries leave out CALLS and TESTS edges below this confidence (ADR 0004). |
| `PRISM_STORE` | Literal | `persistent` | "persistent" is Neo4j + pgvector + the Postgres ledger; "memory" keeps nothing. |
| `PRISM_INDEX_RUN_STALE_AFTER_S` | int | `900` | A running index run with no heartbeat for this long is treated as crashed (ADR 0006). |
| `PRISM_EMBEDDING_MODEL` | str | `nomic-embed-text` | Code embeddings come from the host Ollama (ADR 0005). |
| `PRISM_EMBEDDING_BATCH_SIZE` | int | `16` |  |
| `PRISM_EMBEDDING_MAX_CHARS` | int | `6000` | nomic-embed-text reads 2048 tokens; about 6000 characters of code fits. |
| `PRISM_EMBEDDING_DOCUMENT_PREFIX` | str | `search_document: ` | nomic-embed-text expects a task prefix; set to "" for models that do not. |
| `PRISM_CONTEXT_MODE` | Literal | `graph+vector` | Retrieved context for the verifier and the frontier path (ADR 0007). |
| `PRISM_CONTEXT_BUDGET_TOKENS` | int | `1500` | Per hunk. |
| `PRISM_CONTEXT_HOPS` | int | `1` |  |
| `PRISM_CONTEXT_VECTOR_K` | int | `8` |  |
| `PRISM_CONTEXT_SNIPPET_MAX_LINES` | int | `40` |  |
| `PRISM_CONTEXT_MIN_ITEM_TOKENS` | int | `40` | A truncated item smaller than this is dropped instead. |
| `PRISM_RERANKER` | Literal | `cross-encoder` |  |
| `PRISM_RERANKER_MODEL` | str | `Xenova/ms-marco-MiniLM-L-6-v2` |  |
| `PRISM_TOKEN_COUNTER` | Literal | `chars` |  |
| `PRISM_TOKEN_CHARS_PER_TOKEN` | float | `4.0` |  |
| `PRISM_MODEL_CACHE_DIR` | str | `~/.cache/prism` | Downloaded models (reranker, tokenizer) live here. |
| `PRISM_GATE` | Literal | `pass-all` | The gate decides which hunks go to the reviewer (ADR 0008). "pass-all" lets every hunk through until a trained model is in the directory below. |
| `PRISM_GATE_MODEL_DIR` | str | `models/gate` |  |
| `PRISM_MODEL_CONCURRENCY` | int | `4` | Review pipeline. Model calls in flight at once; more only queue in Ollama. Start value from the reviewer throughput runs with OLLAMA_NUM_PARALLEL=4. |
| `PRISM_REVIEWER_TIMEOUT_S` | float | `180.0` |  |
| `PRISM_REVIEWER_RETRIES` | int | `2` | A reply that is not valid JSON is retried this many times, then dropped. |
| `PRISM_REVIEW_MAX_HUNKS` | int | `40` | At most this many hunks of a PR go to a model; the rest are listed as not reviewed. |
| `PRISM_REVIEW_TIME_LIMIT_S` | float | `600.0` | Hunks not started by then are listed as not reviewed. |
| `PRISM_REVIEW_MAX_COMMENTS` | int | `20` |  |
| `PRISM_REVIEW_WORK_DIR` | str | `.prism-work` | Cached replies and verifier batches for `prism review` (ADR 0011). |
| `PRISM_VERIFIER` | Literal | `handoff` | "handoff": a Claude model writes verdicts into the work directory. "rules": no model check. |
| `PRISM_FRONTIER_MODEL` | str | `claude-opus-5-5` | "api": Claude through the Anthropic SDK (`uv sync --group frontier`, ANTHROPIC_API_KEY). |
| `PRISM_FRONTIER_EFFORT` | Literal | `high` |  |
| `PRISM_FRONTIER_PRICE_IN_PER_MTOK` | float | `4.0` | USD per million tokens, for PRReview.cost_usd. Defaults are claude-opus-5-5's list price. |
| `PRISM_FRONTIER_PRICE_OUT_PER_MTOK` | float | `20.0` |  |
| `PRISM_VERIFIER_MIN_CONFIDENCE` | float | `0.6` | A comment the verifier keeps below this confidence is dropped. |
| `PRISM_SAST_TOOLS` | str | `bandit,semgrep,ruff,pmd` | Static analysis on the changed files (ADR 0012). Comma-separated; "" turns SAST off. |
| `PRISM_SEMGREP_CONFIG` | str | `p/owasp-top-ten` | Semgrep downloads a registry ruleset on first use; a local rules file works too. |
| `PRISM_PMD_HOME` | str | "" | PMD install directory (holds bin/pmd); unset skips PMD. |
| `PRISM_SAST_TIMEOUT_S` | float | `300.0` |  |
| `PRISM_SAST_COMMENT_MIN_SEVERITY` | Literal | `medium` | A finding on an added line at or above this becomes a comment for the verifier. |
| `PRISM_ESCALATE` | bool | `True` | Routing (ADR 0013): security-sensitive and large hunks go to the frontier model (Claude, through the verifier) instead of the local reviewer. Needs a model verifier. |
| `PRISM_ESCALATE_MIN_LINES` | int | `60` |  |
| `PRISM_ROUTE_ALL` | bool | `False` | Evaluation setting: every planned hunk goes to the frontier model, none to the local one. |
| `PRISM_REVIEW_UNIT` | Literal | `hunk` | Evaluation setting, with route_all: "pr" shows Claude the whole PR in one request instead of one hunk per request. |
| `PRISM_REVIEWER_CONTEXT` | bool | `False` | Evaluation setting: the local reviewer also sees retrieved code before each hunk (it was trained on the hunk alone). |
| `PRISM_TRACING` | Literal | `off` | Tracing and prompt management (ADR 0014). "langfuse" needs `uv sync --group observability` and the `langfuse` Compose profile; the keys below are the profile's local defaults. |
| `PRISM_LANGFUSE_HOST` | str | `http://127.0.0.1:3001` |  |
| `PRISM_LANGFUSE_PROJECT_ID` | str | `prism` |  |
| `PRISM_LANGFUSE_PUBLIC_KEY` | str | `pk-lf-prism-local-dev` |  |
| `PRISM_LANGFUSE_SECRET_KEY` | SecretStr | (secret) |  |
| `PRISM_LANGFUSE_ENVIRONMENT` | str | `development` |  |
| `PRISM_PROMPT_SOURCE` | Literal | `code` | "langfuse": read prompts from Langfuse by label, falling back to the code's text. |
| `PRISM_PROMPT_LABEL` | str | `production` |  |
| `PRISM_REVIEW_STORE` | Literal | `postgres` | Where `prism review` stores reviews (ADR 0015). "postgres" is the Compose Postgres; a review still finishes when it is down. |
| `PRISM_GITHUB_APP_ID` | str | "" | GitHub App (ADR 0017). The private key stays outside the repo; only its path goes here. |
| `PRISM_GITHUB_PRIVATE_KEY_PATH` | str | "" |  |
| `PRISM_GITHUB_WEBHOOK_SECRET` | SecretStr | "" |  |
| `PRISM_GITHUB_API_URL` | str | `https://api.github.com` |  |
| `PRISM_APP_CHECKOUT_DIR` | str | `.prism-work/checkouts` | Shallow checkouts of PR heads, for SAST and retrieval. |
| `PRISM_APP_POLL_INTERVAL_S` | float | `30.0` | Polling mode: seconds between scans of the installed repos' open PRs. |
| `PRISM_APP_MAX_CHANGED_LINES` | int | `3000` | Skip PRs with more changed lines than this (a review of a huge PR helps nobody). |
| `PRISM_APP_QUEUE` | Literal | `inline` | "celery": the webhook queues reviews for `prism app worker` (ADR 0018); "inline": the web process reviews them itself, one at a time. |
| `PRISM_APP_REVIEWS_PER_INSTALLATION` | int | `1` | Reviews running at once for one installation (the per-installation limit). |
| `PRISM_APP_POLL_WITH_BEAT` | bool | `False` | Let `prism app beat` also poll for open PRs, as a fallback for missed webhooks. |
| `PRISM_APP_POST_EMPTY` | Literal | `first` | A review with no comments: "first" posts it only if PRism has not reviewed the PR before, "always" posts every one, "never" posts none. |
| `PRISM_APP_FEEDBACK_WINDOW_DAYS` | int | `14` | Feedback on posted comments (ADR 0019): how far back reactions are read, how often. |
| `PRISM_APP_FEEDBACK_INTERVAL_S` | float | `900.0` |  |
| `PRISM_FEEDBACK_DATASET` | str | `prism-comment-feedback` | Langfuse dataset that collects comments people rated, for the next training round. |
