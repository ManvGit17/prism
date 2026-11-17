# ADR 0016: Review tools over MCP, with the client as verifier

Status: accepted.

## Context

I wanted PRism available as an MCP server with review tools (`review_diff`, `explain_finding`,
`sast_evidence`, `impacted_tests`, `review_history`), so that Claude Code can ask PRism to review
its own uncommitted diff before it commits. Two things made this less direct than it sounds. The
pipeline was written for the CLI: it printed to stdout and exited through typer, and stdout is
the MCP protocol. And the verifier is a Claude handoff (ADR 0011): a review stops until someone
answers the verification requests.

## Decision

**One service for every front end.** `prism/review/service.py` holds the review: it builds the
gate, reviewer, verifier, SAST, retrieval, tracing and prompts from the settings, runs the graph
and stores the result. It never prints; notes go to a callback and failures raise `ReviewError`
with the CLI's exit code. `prism review` is a thin wrapper, and the MCP server and the App worker
call the same function.

**The MCP client is the verifier.** Whoever calls `review_diff` over MCP is a Claude session, so
the handoff needs no files passed around by hand: when the review stops, `review_diff` returns
status `awaiting_verification` with the requests and the rubrics in the result. The client
answers them with `submit_answers(work_dir, verdicts, reviews)`, which validates every answer
against the same schema the handoff uses and writes it next to the requests, and then calls
`review_diff` again; the cached replies make that second call fast. `submit_answers` writes only
inside `PRISM_REVIEW_WORK_DIR`. The server's instructions tell the client that everything inside
a request is untrusted data.

**Tools.** `review_diff(repo_path, diff=None)` reviews the given diff, or `git diff HEAD` (staged
and unstaged changes to tracked files; untracked files are not included, and the result says so).
Its retrieval indexes the checkout in memory by default, so it needs no services. `submit_answers`
is described above. `explain_finding(finding_id)` returns who wrote a comment, whether it was
posted, the decision, the verifier's words, the evidence, the SAST findings in the hunk and the
hunk. `sast_evidence(repo_path, path)` runs the configured tools on one file.
`impacted_tests(repo_path, diff=None)` lists the tests that reach the changed code through the
graph, with a pytest command. `review_history` and `pending_reviews` read the review store; the
second lists reviews waiting for verification, which is how the App's held reviews (ADR 0017)
reach a Claude session.

**Store.** The server uses Postgres when it is up and an in-memory store otherwise, so the tools
work with Docker stopped; reviews then last as long as the server.

**Subprocesses get an empty stdin.** Under MCP the server's stdin is the protocol pipe. A `git
diff` started with the inherited stdin hung until its 60 s timeout on Windows. Every subprocess
PRism starts (git, Bandit, Semgrep, Ruff, PMD) runs with `stdin=DEVNULL`.

## Results

I had PRism review its own staged changes (13 hunks in 9 files, before commit) through the
official MCP client over stdio, as Claude Code would. The first `review_diff` returned 6 comments
to verify and 9 hunks escalated to Claude (large or security-sensitive). A Claude session
answered all 15 from the tool's result alone; `submit_answers` accepted 15 and the second
`review_diff` finished in 28 s: risk low, no comment posted, 7 dropped. The one real issue Claude
found (`git_diff` errors escaping the tool) was rated 0.55, under the threshold, and I fixed it
before the commit anyway. `impacted_tests` listed 11 tests for the change.

## Consequences

- A review over MCP is two or three tool calls, not one, unless `verifier="api"` or `"rules"`.
- Many hunks count as security-sensitive just for calling `subprocess` (ADR 0010's regex), so
  routing sends a lot of this repo's own code to Claude. That rate is worth measuring.
- The server runs as a stdio process from `uv run --group mcp --group sast prism mcp`, so the
  optional groups have to be installed on the machine that runs Claude Code.
