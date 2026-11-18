# Troubleshooting

Problems you are likely to meet when running PRism, what causes them, and how to fix them. Each entry comes from how the code behaves or from a decision recorded in an ADR. For setup, see [Running the services](running-the-services.md).

## Exit codes

| Code | Where | Meaning |
|---|---|---|
| 0 | `prism review` | The review finished and the JSON is on stdout |
| 2 | `prism review`, `prism bench ...` | **Not a failure.** The handoff verifier is waiting for Claude's answers. Also used for a bad option value: an unknown verifier or context mode, or `PRISM_REVIEW_UNIT=pr` without `PRISM_ROUTE_ALL=true` |
| 3 | `prism review` and most commands | The review or command could not run: the persistent store is down, the gate model is missing, a needed dependency group is not installed, or (for the App) its settings are missing |
| 4 | `prism review` | With `PRISM_VERIFIER=api`, some answers did not come back from the API; the errors are printed above |
| 1, 2 | `prism graph ...` | No symbol matched (1), or the name is ambiguous or the store holds several repos (2) |

### Exit code 2 from `prism review`

This is the verifier handoff ([ADR 0011](../adr/0011-claude-verifier-handoff.md)). The first run wrote request files and rubrics into the work directory (`.prism-work/<repo>-<head sha>-<diff hash>/verify`, or the folder named by `--work`) and printed how many answers are needed. A Claude session writes `verdicts_NN.jsonl` for each `requests_NN.jsonl` (following `RUBRIC.md`) and `reviews_NN.jsonl` for each `escalations_NN.jsonl` (following `REVIEW_RUBRIC.md`). Then run the same command again: it reads the answers and finishes with exit code 0.

There can be a second round. If the verifier escalates a hunk, the second run exits with code 2 again, with a new set of requests. Answer them and run once more.

Over MCP you never see exit codes: `review_diff` returns the status `awaiting_verification`, and Claude Code answers with `submit_answers`.

To avoid the handoff, set `ANTHROPIC_API_KEY`, install the `frontier` group and set `PRISM_VERIFIER=api`, or use `--verifier rules` to keep only the deterministic checks (no model verification).

## Ollama

### Port conflicts

PRism talks to Ollama at `PRISM_OLLAMA_URL` (default `http://127.0.0.1:11434`). If two Ollama servers are installed (for example one inside WSL and one on Windows), whichever started first holds port 11434, and it may not have your models. Symptoms are model-not-found errors from a server you thought you had set up.

* Check which server answers: `curl http://127.0.0.1:11434/api/tags` lists the models that server has.
* Either stop the other server, or start yours on another port (`OLLAMA_HOST=127.0.0.1:11435 ollama serve`) and set `PRISM_OLLAMA_URL=http://127.0.0.1:11435`.

Use `127.0.0.1` rather than `localhost`. On Windows `localhost` tries `::1` first, and in measurements one request through `localhost` took 0.47 s against 0.054 s through `127.0.0.1` ([ADR 0005](../adr/0005-embeddings.md)).

### Models not pulled

The reviewer defaults to `prism-reviewer:v2`, which exists only after you build it. Pull the base model and point PRism at it:

```bash
ollama pull qwen2.5-coder:3b
ollama pull nomic-embed-text
```

```bash
PRISM_REVIEWER_MODEL=qwen2.5-coder:3b
```

The fine-tuned reviewer is on Hugging Face as `Maverick0824/prism-reviewer-3b` (GGUF and a Modelfile; Qwen Research License, non-commercial). Build it with `ollama create prism-reviewer:v2 -f Modelfile`.

When the reviewer call fails, PRism retries (`PRISM_REVIEWER_RETRIES`, default 2). A hunk that still has no valid reply is dropped and listed in the review summary as not reviewed, so a missing model shows up as a review with no local comments and a list of failed hunks, not as a crash. A reply is retried the same way when it is not valid JSON.

`nomic-embed-text` is needed for vector context (the default `PRISM_CONTEXT_MODE` is `graph+vector`). Without it, use `--mode graph` or pull the model.

### Slow reviews

Ollama only runs as many requests as it has parallel slots. `PRISM_MODEL_CONCURRENCY` (default 4) matches `OLLAMA_NUM_PARALLEL=4`; set both together. A large PR is bounded by `PRISM_REVIEW_MAX_HUNKS` (40) and `PRISM_REVIEW_TIME_LIMIT_S` (600); hunks left over are listed as not reviewed.

### From a container

The review worker in Compose reaches the host's Ollama at `http://host.docker.internal:11434` (`PRISM_WORKER_OLLAMA_URL`). Ollama must listen on an address the container can reach, not only on `127.0.0.1`.

## Postgres or Neo4j down

`prism review` with the default `PRISM_STORE=persistent` needs Neo4j and Postgres for retrieval, and fails with exit code 3 and a message such as `Neo4j at bolt://127.0.0.1:7687: ...` or `Postgres at 127.0.0.1:5432: ...`, followed by "Start the services (make up) or use --store memory."

What still works:

| Down | Still works |
|---|---|
| Neo4j and Postgres | `prism review --store memory` (indexes the checkout in memory), the MCP server (it falls back to an in-memory store), `--no-save` or `PRISM_REVIEW_STORE=off` |
| Postgres only | Reviews finish; they are just not saved. The `postgres` review store (`PRISM_REVIEW_STORE`) never fails a review. Persistent retrieval with vectors does need it |
| Redis | `PRISM_APP_QUEUE=inline` (the web process reviews). With `celery`, queued reviews wait |
| Ollama | Static analysis and the rule checks still run, but the local reviewer produces no comments (its hunks are listed as failed), and vector context is unavailable; use `--mode graph` |

Start the core services with `make up`. If a service is unhealthy, `make logs` shows why. After a crash, a running index run with no heartbeat for `PRISM_INDEX_RUN_STALE_AFTER_S` (900 s) is treated as crashed and can be restarted.

The message `note: '<repo>' has no complete index run; run prism index first` means the repository was never indexed in the persistent store. Run `prism index PATH`, or use `--store memory`.

## Static analysis tool missing

PRism runs Bandit, Semgrep, Ruff and PMD on the changed files, and a tool it cannot find is skipped, never an error ([ADR 0012](../adr/0012-sast-evidence.md)). The review summary says which: "SAST findings in the changed files: ...; not run: bandit (not installed (uv sync --group sast))".

* Bandit and Semgrep come from the `sast` group: `uv sync --group sast`.
* PMD needs Java and a PMD install; set `PRISM_PMD_HOME` to the directory holding `bin/pmd`. Without it PMD is reported as "PRISM_PMD_HOME not set or has no bin/pmd" or "java not found".
* Semgrep downloads its ruleset (`PRISM_SEMGREP_CONFIG`, default `p/owasp-top-ten`) the first time, so it needs network access then. For offline use, point the setting at a local rules file.

In a BugBench run, the summary records the tools that actually ran in `sast_ran`. Check it when two runs of the same configuration differ: a run made before Bandit was installed has `sast_ran` without `bandit`, and a Bandit finding in the later run can change a result. This happened during the ablations ([ADR 0024](../adr/0024-ablation-gate.md)).

## First review is slow or fails offline

The first run downloads a small reranker model from Hugging Face (`PRISM_RERANKER_MODEL`) into `PRISM_MODEL_CACHE_DIR` (`~/.cache/prism`). Later runs use the cache. With `PRISM_GATE=onnx`, the gate model must be in `PRISM_GATE_MODEL_DIR` (`models/gate`); the published gate is `Maverick0824/prism-gate`. The default `PRISM_GATE=pass-all` needs no model and lets every hunk through. If the gate model is configured but the files are missing, the review fails with exit code 3 and a "not found" message naming the file.

## Langfuse

### I cannot log in

First start creates the `prism` organisation and project and its API keys, but **no user**. Open http://localhost:3001 and sign up with your own email and password; you become the owner of the `prism` project ([ADR 0014](../adr/0014-langfuse-tracing-and-prompts.md)).

### No traces appear

* `PRISM_TRACING` must be `langfuse` (the default is `off`), and the `observability` group installed: `uv sync --group observability`. Without the group, a review fails with a message saying so.
* The keys in `.env` (`PRISM_LANGFUSE_PUBLIC_KEY`, `PRISM_LANGFUSE_SECRET_KEY`) must match the project's. The defaults match the Compose profile's own defaults; if you changed one side, change the other.
* `prism prompts push` exits with code 3 and "Langfuse rejected the keys" when authentication fails.
* A review never fails over a prompt: with `PRISM_PROMPT_SOURCE=langfuse` it falls back to the prompt in the code when Langfuse is down or has no such prompt.

### langfuse-web keeps restarting

Under a 1 GB limit the web container ran out of JavaScript heap while starting. The Compose file sets `NODE_OPTIONS=--max-old-space-size=1024` and a 1.5 GB limit for that reason; if you lowered the limit, raise it again. If Docker has less memory than the services need, start Langfuse only when you look at traces (see the [memory budget](../memory-budget.md)).

## GitHub App: webhook not arriving

Work through these in order.

1. **Is the server running?** `prism app serve` listens on `127.0.0.1:PORT/webhook` (default port 8080). It exits with code 3 and "set PRISM_GITHUB_WEBHOOK_SECRET in .env" if the secret is empty. The App id and private key path must be set too.
2. **Can GitHub reach it?** The server binds to `127.0.0.1`, so GitHub cannot call it directly from the internet. For local development, forward deliveries with a smee.io channel (set it as the App's webhook URL and run the smee client towards `http://127.0.0.1:8080/webhook`), or add `--poll`, which scans open PRs every `PRISM_APP_POLL_INTERVAL_S` (30 s). `prism app poll` does the same without the server ([ADR 0017](../adr/0017-github-app.md)).
3. **Signature.** A delivery whose `X-Hub-Signature-256` does not match `PRISM_GITHUB_WEBHOOK_SECRET` gets a 401. The secret in `.env` must be exactly the one set in the App's settings. Check the App's "Recent Deliveries" page on GitHub for the status code.
4. **Is the event one PRism reviews?** `pull_request` events with `opened`, `synchronize`, `reopened` or `ready_for_review` on a non-draft PR are accepted (HTTP 202). Anything else is answered with status `ignored`. Draft PRs are skipped until they are ready for review.
5. **Is the queue the problem?** With `PRISM_APP_QUEUE=celery` the webhook only queues a job, so you also need Redis (`make up`), `prism app worker`, and, for held reviews, `prism app beat`. Repeated deliveries and old commits are dropped on purpose: a task stops as `duplicate` or `superseded` ([ADR 0018](../adr/0018-review-queue.md)).
6. **Too big?** PRs with more than `PRISM_APP_MAX_CHANGED_LINES` (3000) changed lines are skipped.

### The review was received but nothing was posted

With the handoff verifier, the App holds a review until Claude has answered the verification requests ([ADR 0017](../adr/0017-github-app.md)). Held reviews are listed by the MCP `pending_reviews` tool; a Claude session answers them with `submit_answers`, and the next poll, `prism app resume`, or beat's per-minute `resume_held` posts the review. With `PRISM_VERIFIER=api` it posts without waiting. A review with no comments is posted only the first time for a PR unless `PRISM_APP_POST_EMPTY=always`.

If someone pushed while the review was waiting, the old result is dropped as superseded, because its line numbers may be stale; the new push gets its own review.

## Windows notes

* **Use `127.0.0.1`, not `localhost`**, for every host setting. Compose publishes on `127.0.0.1` only, and on Windows `localhost` tries `::1` first, which cost 2 s per Neo4j connect and up to 130 s per Postgres connect.
* **Line endings.** The repository sets `* text=auto eol=lf` in `.gitattributes`. A checkout that converts to CRLF changes every line of a diff and the hashes of hunks. BugBench clones repositories with `core.autocrlf=false` for the same reason. If your own repository uses CRLF, expect noisy diffs.
* **The review worker.** Celery's default pool does not run on Windows. `prism app worker` uses the solo pool there, so one worker process reviews one PR at a time. The container worker is for Linux hosts.
* **Maven for BugBench.** The jsoup cases run `mvn` with `-Dmaven.repo.local`. Set `PRISM_BENCH_MVN` to the full path of `mvn` (on Windows, `mvn.cmd`) if it is not on `PATH`, and `PRISM_BENCH_M2` for the local repository (default `~/.m2/repository`).
* **Subprocesses and stdin.** Every subprocess PRism starts (git, Bandit, Semgrep, Ruff, PMD) gets an empty stdin. Under MCP, stdin is the protocol pipe, and a `git diff` that inherited it hung until its 60 s timeout on Windows ([ADR 0016](../adr/0016-mcp-review-tools.md)). If you add your own subprocess calls, do the same.
* **PMD** is found as `pmd.bat` under `PRISM_PMD_HOME/bin` on Windows.
* **The `.pem` file.** Keep the GitHub App's private key outside the repository and put only its path in `PRISM_GITHUB_PRIVATE_KEY_PATH`.

## Still stuck

Run the review with `prism review ...` and read stderr: notes, SAST status, the verifier's errors and the saved review id are printed there. In the MCP server, `explain_finding(finding_id)` shows why a comment was posted or dropped, and the dashboard (`prism dashboard`, port 8090) shows the verifier's reason for every dropped comment.
