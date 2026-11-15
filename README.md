# PRism

A repo-aware AI code reviewer. It parses a repository into a code knowledge graph, retrieves
callers, callees and tests for each changed function, and reviews pull-request hunks with a small
local model whose comments a verifier then checks.

**Status:** Phases 1 to 6 done: code graph for Python and Java, context retrieval for a diff, a
gate classifier, a fine-tuned reviewer model ([results](eval/reports/sft.md)), the review
pipeline with static analysis, a Claude verifier and tracing
([what it does on real diffs](eval/reports/review_pipeline.md)), and around it a GitHub App on a
Celery queue, an MCP server for Claude Code, feedback from reactions into Langfuse and a
dashboard. No benchmark yet (Phase 7). The plan is in [PLAN.md](PLAN.md).

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

## Use it from Claude Code

PRism's review tools are an MCP server (ADR 0016):

```bash
uv sync --group mcp --group sast
claude mcp add prism -- uv run --directory /path/to/prism --group mcp --group sast prism mcp
```

Then ask Claude Code to review your uncommitted changes with PRism. `review_diff` reviews
`git diff HEAD`; when it needs verification it hands the requests to Claude Code, which answers
them with `submit_answers` and asks again. `explain_finding`, `sast_evidence`, `impacted_tests`,
`review_history` and `pending_reviews` cover the rest. It works without Docker: retrieval indexes
the checkout in memory and reviews are kept for the session when Postgres is down.

## Run it as a GitHub App

Register an App on your account (permissions: Pull requests read and write, Contents read-only;
event: Pull request), install it on a repo, and put its id, the path to its private key and the
webhook secret in `.env` (see `.env.example`). Then:

```bash
uv sync --group app --group sast --group mcp
uv run prism app serve --port 8080 --poll     # webhooks on 127.0.0.1:8080/webhook, plus polling
npx smee-client --url https://smee.io/<your channel> --target http://127.0.0.1:8080/webhook
```

The smee channel is the App's webhook URL during local development; `--poll` alone works with no
tunnel. Reviews are held until their verification is answered (ADR 0017): a Claude Code session
with PRism's MCP server finds them with `pending_reviews`, answers them, and the next poll or
`prism app resume` posts the review with its inline comments.

To run reviews on a queue instead of inside the web process (ADR 0018), set
`PRISM_APP_QUEUE=celery` and start a worker and beat next to `prism app serve`:

```bash
uv run prism app worker      # on Windows this uses Celery's solo pool
uv run prism app beat        # posts answered held reviews every minute
```

`docker compose --profile worker up -d --build` runs both in containers instead, on hosts where
the worker can reach Ollama.

Reactions on PRism's comments feed back into Langfuse (ADR 0019): beat reads 👍/👎 every 15
minutes, replies and resolved threads arrive by webhook (subscribe the App to "Pull request review
comment" and "Pull request review thread"), and rated comments become scores on the review's
trace and items in the `prism-comment-feedback` dataset. `prism app feedback` runs one pass.

## Dashboard

```bash
cd dashboard && npm ci && npm run build && cd ..
uv run prism dashboard                         # http://127.0.0.1:8090
```

Reviews with their cost, time, comments posted out of considered and reactions; each review's
comments, posted and dropped, with the verifier's reasons; who wrote the posted comments and
where hunks were routed; and the eval reports (ADR 0020). Read-only.

## BugBench

```bash
export PRISM_BENCH_ROOT=D:/res-proj-plan/bench   # clones, worktrees, venvs, runs: outside the repo
uv run prism bench run $(ls bugbench/cases/*.toml | grep -v slice) --run mid-01 \
    --report eval/reports/bugbench_mid.md
```

Each case is a real commit (ADR 0021): with one planted defect at a known line, a commit that
introduced a bug fixed later, or a clean commit. The run reviews every case, then stops with
exit code 2 and puts all the open Claude requests of all the cases in one folder,
`runs/<run>/handoff/round_NN`, with a `README.md` saying which file to answer with which rubric.
Answer them (a Claude session or subagent) and run the same command again until it prints the
summary: recall at ±3 lines for injected and real bugs, the false-positive rate on clean PRs,
comments per PR, which model found each defect, and first-run time.

New injected cases come from `prism bench generate REPO --out bugbench/cases/REPO.toml`, which
needs the repo's test environment (`bugbench/repos.toml`; for Java, `PRISM_BENCH_MVN` and
`PRISM_BENCH_M2`). It keeps a mutation only when the repo's tests pass without it and fail
with it.

The PR-Agent baseline (ADR 0022) runs on the same cases with Claude answering through the same
kind of handoff, then a report compares the two runs:

```bash
uv run prism bench baseline $(ls bugbench/cases/*.toml | grep -v slice) --run pra-01
uv run prism bench compare mid-01 pra-01 --report eval/reports/bugbench_vs_pr_agent.md
```
