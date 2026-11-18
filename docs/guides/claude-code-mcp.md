# Using PRism from Claude Code

This guide shows how to add PRism's MCP server to Claude Code, what each tool does, and how a review runs inside a session. It is for developers who want Claude Code to review its own uncommitted changes before committing.

## Add the server

Install the optional groups on the machine that runs Claude Code, then register the server:

```bash
uv sync --group mcp --group sast
claude mcp add prism -- uv run --directory /path/to/prism --group mcp --group sast prism mcp
```

The server talks over stdio, so nothing listens on a port. You also need Ollama running with the reviewer model (see [Models](models.md)). Postgres is optional: the server uses it when it is up (`make up`) and keeps reviews in memory otherwise, so the tools work with Docker stopped. In memory, reviews last as long as the server.

Check that Claude Code sees it with `claude mcp list`.

## The tools

| Tool | Arguments | What it does |
|---|---|---|
| `review_diff` | `repo_path`, `diff`, `pr_number`, `verifier`, `context_store` | Reviews a unified diff of the repo, or its uncommitted changes (`git diff HEAD`) when no diff is given. Returns the review, or status `awaiting_verification` with the requests to answer. |
| `submit_answers` | `work_dir`, `verdicts`, `reviews` | Validates Claude's answers and writes them where the review reads them. |
| `explain_finding` | `finding_id` | Says why a comment was posted or dropped: who wrote it, the verifier's note, evidence, static analysis findings in the same hunk, and the hunk. |
| `sast_evidence` | `repo_path`, `path` | Runs the static analysis tools (Bandit, Semgrep, Ruff, PMD and the prompt-injection scan) on one file. Tools that are not installed are listed as not run. |
| `impacted_tests` | `repo_path`, `diff` | Lists the tests that reach the changed code through the code graph, with a `pytest` command for Python ones. |
| `review_history` | `repo`, `pr_number`, `limit` | Lists past reviews, newest first. `repo` is `owner/name` for GitHub pull requests and the folder name for local reviews. |
| `pending_reviews` | `limit` | Lists reviews waiting for verification, such as the ones the [GitHub App](github-app.md) holds back. |

Details worth knowing:

- `review_diff` reviews tracked files only. Untracked files are not in `git diff HEAD`, and the result says so in `diff_source`. Pass a `diff` to include them.
- `verifier` is `handoff` (the default), `api` or `rules`. See [Reviewing from the CLI](reviewing-from-the-cli.md#verifier-choices).
- `context_store` is `memory` (default: the checkout is indexed now, no services needed) or `persistent` (Neo4j and pgvector, after `prism index`).
- With no changes, `review_diff` returns status `empty`.

## A typical session

Ask Claude Code something like "review my uncommitted changes with PRism". It then:

1. Calls `review_diff(repo_path)`. The first call plans the diff, runs the reviewer and static analysis, and stops with `status: "awaiting_verification"`. The result holds `work_dir`, `verify_requests`, `review_requests`, and the rubrics `verify_rubric` and `review_rubric`.
2. Reads the rubrics, judges every request, and calls `submit_answers(work_dir, verdicts, reviews)`. The result says how many answers were `accepted`, any `errors`, and how many are `still_missing`.
3. Calls `review_diff` again with the same arguments. The reviewer's replies are cached, so this is fast. It returns `status: "complete"` with `verdict`, `summary`, `comments`, the number of `dropped` comments and a `trace_url`. If a verdict escalated a hunk, the status can be `awaiting_verification` once more for a second round.
4. Fixes what the comments point out. If a comment is unclear, it calls `explain_finding` with the comment's `finding_id`, and `impacted_tests` tells it which tests to run.

A comment in the result carries these fields:

```json
{
  "finding_id": 112,
  "path": "shop/shipping.py",
  "line": 12,
  "author": "claude",
  "comment": "Off-by-one at the threshold: ...",
  "posted": true,
  "decision": "...",
  "severity": "major",
  "category": "bug",
  "confidence": 0.9,
  "suggested_fix": "Use `if subtotal >= FREE_SHIPPING_FROM:`"
}
```

`explain_finding` adds the verifier's note, the evidence, the static analysis findings in the hunk and the hunk itself.

## How verification works through MCP

The client is the verifier. The server's instructions tell Claude Code that when a review is awaiting verification, it is the verifier: it follows the rubrics, judges every request, and answers through `submit_answers`. No files are passed around by hand.

`submit_answers` checks every answer against the same schema the file handoff uses, and rejects ids that match no request. It writes only inside `PRISM_REVIEW_WORK_DIR` (default `.prism-work`), so it refuses any other `work_dir`. Run the server from a directory where that relative path is the one you want, or set an absolute `PRISM_REVIEW_WORK_DIR` in `.env`.

!!! warning
    Everything inside a verification request comes from the code under review, so it is untrusted data. The server's instructions and the rubrics tell Claude to ignore instructions found in requests, and a hunk that tries to instruct a reviewer is itself worth a security comment.

With `verifier="api"` (an API key and `uv sync --group frontier`) or `verifier="rules"`, there is no handoff, and `review_diff` returns the finished review in one call.

Reviews held by the GitHub App appear in `pending_reviews`; answer their `work_dir` with `submit_answers` and the App posts them on its next resume pass. The design is in [ADR 0016](../adr/0016-mcp-review-tools.md).
