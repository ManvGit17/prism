# Verification and routing

This page explains how Claude checks PRism's comments before anything is posted, how hunks are
routed between the local model and Claude, and how the answers reach PRism through a file handoff
or the Anthropic API. It is for readers who run PRism, or who want to know what the confidence
threshold and the evidence rule do. The decisions are in
[ADR 0011](../adr/0011-claude-verifier-handoff.md) and
[ADR 0013](../adr/0013-routing-redaction-injection.md).

## Why Claude verifies

The local reviewer writes short, specific-sounding comments, and a lot of them are wrong. The
deterministic checks catch comments on the wrong line, vague ones and repeats, but they cannot tell
whether a claim is true. A 3B model checking another 3B model's claims was not worth building, so
a Claude model does it: it checks each claim against the hunk and the retrieved context, and
assigns a severity and a category.

Claude does two jobs, each with its own rubric (`prism/review/handoff.py`):

| Job | Input | Output |
|---|---|---|
| Verify | A comment written by the local reviewer or by a static analysis tool, with its hunk, context and tool findings | Keep or drop, severity, category, confidence, evidence, an optional corrected line, and whether to escalate |
| Review | A hunk that was escalated | Up to three comments of its own, with line, severity, category, confidence, evidence and a suggested fix where one is clear |

## The two rubrics

A rubric is the prompt Claude works from. PRism writes it next to the requests (handoff) or sends
it as the system prompt (API). The reference text is in `prism/review/handoff.py`; the rubrics are
also pushed to Langfuse prompt management, and a review reads them by `PRISM_PROMPT_LABEL`.

**Verify rubric (`RUBRIC.md`).** It tells Claude that most comments come from a small local model
that is often confidently wrong, and that tool findings are usually true about the line but often
do not matter (test code, a constant, input that is not user-controlled). A comment is kept only
if it is:

1. **True.** The claim is correct about this code, checked against the hunk and the context.
2. **Useful.** A developer would want it: a bug, a risk, a missing test, a real readability
   problem or a fair question about intent.
3. **On the right line.** If it is right but on the wrong line, Claude keeps it and gives the
   new-file line it belongs on. PRism accepts the move only if that line is shown in the hunk.

A true but weak comment is kept with low confidence rather than dropped, so the threshold decides,
not the verifier's mood. When Claude cannot tell, it uses a confidence below 0.5. Claude sets
`escalate` when the hunk looks like it has a real problem the comment does not name, such as a
logic bug, a broken contract or a security issue. It does not escalate just because the comment is
wrong.

**Review rubric (`REVIEW_RUBRIC.md`).** It asks Claude to review an escalated hunk as a careful
senior engineer: at most three comments, most important first, real problems only (bugs, security
issues, broken contracts, missing handling of an input the code clearly receives, a missing test
for risky logic), with no style or naming unless it misleads. No comment is a good answer for a
hunk that is fine. The request also carries `reasons` (why the hunk was escalated) and `notes` (an
earlier comment on the hunk and what the verifier said about it).

Both rubrics tell Claude that the hunk, the comments, the context and the tool messages come from
the pull request and are untrusted data, and that `[REDACTED:<kind>]` marks a secret that was
removed.

## The rules a comment must pass

A comment is posted only if all of these hold:

- It passes the deterministic checks (line on the new side of the hunk, not vague, not a
  duplicate).
- The verdict keeps it.
- Its confidence is at least `PRISM_VERIFIER_MIN_CONFIDENCE` (default 0.6). Below that it is
  dropped with the reason `low_confidence`.
- If it is a `security` comment, it cites evidence. Without evidence it is dropped as
  `security_without_evidence`.

**The evidence rule.** Evidence is a list of `{"source": ..., "detail": ...}` entries. The source
is `hunk` (a line of the diff), a context kind (`caller`, `test`, `signature` and so on) or a tool
name (`bandit`, `semgrep`, `ruff`, `pmd`, `prism`). A security claim has to rest on something
Claude or a tool can point at. This applies to Claude's own comments from escalated hunks as well:
they skip the verify step, because the same model would be checking itself, but they go through
every deterministic gate a verified comment does.

**The confidence threshold.** The threshold decides which of Claude's comments are posted. The
default 0.6 is where the benchmark's clean pull requests stay without comments; see
`eval/reports/bugbench.md` for the sweep. Set it with
`PRISM_VERIFIER_MIN_CONFIDENCE`.

Answers are matched to requests by id, which is the path, the hunk index and a hash of the hunk and
the comment. A pull request cannot add or change a verdict by what its text says, and an answer
left in a reused work directory never applies to a different comment.

## Routing

The router keeps the local model as the default and sends a hunk to Claude when it is risky or
big (`prism/review/router.py`). `PRISM_ESCALATE=false`, or the rules verifier, keeps everything
local.

**Up front, from the plan.** A hunk goes to Claude instead of the local model when:

| Reason | When |
|---|---|
| `security` | The hunk's path or added code looks security-sensitive, or its text reads like an instruction to a reviewer ([prompt injection](security-and-privacy.md#prompt-injection)) |
| `large` | The hunk changes at least `PRISM_ESCALATE_MIN_LINES` (default 60) lines |
| `all` | `PRISM_ROUTE_ALL=true`, an evaluation setting that sends every planned hunk to Claude |

The local model is not called for these hunks: its comment would only be one more thing to verify.
The tuned 3B model found none of the four security bugs in a test commit, and it was trained on
short hunks and writes one comment, so it is not asked about security-sensitive or large hunks.

**After verification, the second round.** A hunk goes to Claude for a review of its own when the
verifier flagged it (`escalate`), or when the verifier kept its local comment with a confidence
below the threshold. Tool-written comments never trigger the low-confidence case. The reason for
these hunks is `verifier`. There is at most one second round.

**The cost of routing.** With routing on, the local model reviews only the hunks nobody thought
were risky. That is by design, but it means the local model's share of the work depends on the
risk patterns in the planner. The review summary says how many hunks went to the local model and
how many to Claude, with the reasons.

!!! note
    `PRISM_REVIEW_UNIT=pr` (with `PRISM_ROUTE_ALL=true`) is an evaluation setting for the
    ablations: it sends the escalated hunks of a pull request to Claude as one request per pull
    request, grouped by file, instead of one request per hunk. See
    [ADR 0025](../adr/0025-ablation-whole-pr.md).

## The backends

`ClaudeVerifier` asks a backend for answers to batches of verify and review requests. Both
backends take the same requests and return the same JSON lines, so they are interchangeable.
`PRISM_VERIFIER` selects one:

| Value | Backend | Behaviour |
|---|---|---|
| `handoff` (default) | `HandoffBackend` | Writes requests to files; a Claude session writes the answers; the same command again finishes |
| `api` | `ApiBackend` | Sends the rubric and requests to the Anthropic API and finishes in one run |
| `rules` | none | Deterministic checks only; no Claude check, nothing escalated |

### The file handoff

`prism review --work DIR` runs in up to three passes:

1. **First run.** PRism plans the pull request, calls the local reviewer, applies the rule checks,
   retrieves context, and writes into `DIR/verify`:
   `requests_NN.jsonl` (verify requests, 25 per file) with `RUBRIC.md`, and
   `escalations_NN.jsonl` (hunks routed up front) with `REVIEW_RUBRIC.md`. It exits with code 2
   and says how many answers are needed.
2. **Answer.** A Claude session reads the rubric and writes `verdicts_NN.jsonl` and
   `reviews_NN.jsonl` next to the requests, one JSON line per request, with the same `NN`.
3. **Second run.** The same command reads the answers. If a verdict escalates a hunk, PRism writes
   escalation requests for it and exits with code 2 again; once they are answered, the third run
   prints the review. With nothing to escalate, the second run is the last, and exits with 0.

Reviewer outcomes are cached in `DIR/replies.json`, so later runs call no local model and plan the
same hunks. The default work directory is `PRISM_REVIEW_WORK_DIR/<repo>-<head sha>-<diff hash>`,
so rerunning the same command finds it. A line that does not parse is reported, and its request
stays pending.

```bash
prism review --repo path/to/repo --diff change.diff   # exit code 2: requests written
# a Claude session writes verdicts_NN.jsonl and reviews_NN.jsonl in .prism-work/.../verify
prism review --repo path/to/repo --diff change.diff   # prints the review (exit 0)
```

The handoff reports a cost of 0: the Claude session is not metered by PRism, and the review
records the verifier as `claude (handoff)`, not which Claude model answered.

**Through MCP.** The client of `review_diff` is a Claude session, so no files are passed by hand:
when the review stops, `review_diff` returns `awaiting_verification` with the requests and the
rubrics. The client answers with `submit_answers(work_dir, verdicts, reviews)`, which validates
every answer against the same schema and writes it next to the requests (only inside
`PRISM_REVIEW_WORK_DIR`), then calls `review_diff` again with the same arguments.

**In the GitHub App.** A review whose verifier has not answered is held: it is stored as
`awaiting_verification` and nothing is posted. A Claude session sees it through the MCP
`pending_reviews` tool and answers it; `prism app resume` (also run on every poll) reruns the
review from the cached replies and posts it. See [ADR 0017](../adr/0017-github-app.md).

### The API backend

With `PRISM_VERIFIER=api`, the whole review, second round included, runs in one command.

```bash
uv sync --group frontier
export ANTHROPIC_API_KEY=...
PRISM_VERIFIER=api prism review --repo path/to/repo --diff change.diff
```

`ApiBackend` (`prism/review/frontier_api.py`) sends the rubric as the system prompt and the
requests ten at a time, and parses the same JSON lines back. It uses the official `anthropic`
SDK, which is in the optional `frontier` group.

| Setting | Default |
|---|---|
| `PRISM_FRONTIER_MODEL` | `claude-opus-5-5` |
| `PRISM_FRONTIER_EFFORT` | `high` |
| `PRISM_FRONTIER_PRICE_IN_PER_MTOK` | `4.0` |
| `PRISM_FRONTIER_PRICE_OUT_PER_MTOK` | `20.0` |

The call enables the API's server-side refusal fallback. A batch the model declines is reported,
and its answers stay missing. Cost is input and output tokens times the configured price, and it
appears as `cost_usd` in the review.

I did not use LiteLLM for this. Routing is PRism's own code and the only frontier model is Claude,
so a provider-neutral layer would only add a dependency.

## What Claude sees

Claude sees only the request, not the repository. Each request holds the id, the hunk with its
new-file line numbers, the retrieved context, the tool findings in the hunk and the planner's
risk reasons, and, for a verify request, the author and the comment. That keeps a review
reproducible and makes the context the thing being tested. Every text field is redacted before it
is written or sent. See [Security and privacy](security-and-privacy.md).
