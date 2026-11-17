# ADR 0010: Review pipeline: planner, budget, rule checks, fan-out

Status: accepted.

## Context

The gate (ADR 0008) and the tuned reviewer (ADR 0009) sit behind one command, `prism review`. The
reviewer is `prism-reviewer:v2` on the host Ollama. It writes short, specific-sounding comments,
but a lot of them are wrong, so nothing it writes should reach a PR without checks. My machine
runs about 35 to 45 hunks a minute at 4 parallel requests
(`eval/reports/reviewer_throughput.jsonl`), so a big PR needs a budget.

## Decision

**Graph.** LangGraph, `planner -> Send() per file -> file_reviewer -> verifier -> aggregator`
(`prism/review/graph.py`). Candidate comments from the file reviewers are merged with an
`operator.add` reducer and sorted back into diff order before verification, so the result does not
depend on which file finished first. The verifier stage is described in ADR 0011.

**The gate runs in the planner, not in each file reviewer.** The hunk budget is per PR: to pick
the best K hunks across all files, every hunk needs its gate score first. The gate is cheap (ONNX
on CPU), so scoring all hunks up front costs little.

**Risk score.** Per file, in [0, 1]: 0.5 if the path or the added code looks security-sensitive
(regexes in `prism/review/plan.py`), plus up to 0.3 for size (changed lines / 100) and up to 0.2
for churn (commits touching the file in the last 200, when the checkout has git history). The
weights are a starting point, not tuned.

**Budget.** Hunks the gate passes are ranked by file risk + gate score, and the first
`PRISM_REVIEW_MAX_HUNKS` (40) are reviewed. The rest, and any hunk not started before
`PRISM_REVIEW_TIME_LIMIT_S` (600 s), are listed in the summary as not reviewed with the reason.
A hunk whose path or added code looks security-sensitive skips the gate: the gate was trained on
review comments in general, and missing one of these costs more than one extra model call.
Binary, deleted, lock and generated files are not reviewed and are listed.

**Concurrency.** One `asyncio.Semaphore` per review, sized by `PRISM_MODEL_CONCURRENCY` (4, from
the throughput measurement with `OLLAMA_NUM_PARALLEL=4`), wraps every model call. All the
fanned-out file reviewers share it, so the number of requests in flight never goes above the limit
however many files there are. A test checks this.

**Bad replies.** A reply that is not `{"comment": ...}` is retried twice, the retries with a little
sampling so they can differ, then the hunk is listed as `model_failed`.

**Rule checks before the model verifier.** A comment is dropped if its line is not on the new side
of its hunk, if it is vague (under 8 words and naming nothing in the hunk: the run 2 training
filter of ADR 0009), or if it repeats a comment already kept (same text anywhere in the PR, or a
near copy on the same line). Comments that pass get keyword labels and a fixed confidence of 0.3,
because nothing has checked the claim yet.

**Aggregator.** Comments are capped at `PRISM_REVIEW_MAX_COMMENTS` (20), most severe first. The
verdict is high for any critical comment or major security comment, medium for any major comment
or an unreviewed security-sensitive hunk, low otherwise.

## Consequences

- A first run on a 9-hunk diff with the ONNX gate took 40 s. The gate skipped 3 hunks and all 6
  comments passed the rule checks, but several were plainly wrong. The rule checks catch form, not
  truth: checking the claim is the job of the model verifier (ADR 0011).
- Risk weights and the security regexes are guesses. The benchmark is where to check them.
