# ADR 0021: BugBench, planted defects in real commits

Date: 2026-11-15. Status: accepted for the early slice; the full benchmark builds on it.

## Context

PLAN.md Phase 7 measures the reviewer on pull requests with a known bug at a known line. The
first step is a small slice (10 PRs on one Python repo) run end to end through the current
pipeline, to check the harness and the metric before scaling to 100–150 PRs and the ablations.

## Decision

**Cases** (`bugbench/cases/*.toml`). A case is a real commit of an open-source repo plus one
planted defect: `find` is replaced by `replace` in `path` at that commit. The PR is the whole
commit against its parent, so the defect sits inside a real change (source, tests and docs),
not alone in a one-line diff. `find` must occur exactly once, which the loader checks. The
defect's line is the first line of the new file that the replacement changes; for a deletion it
is the line now in its place. Each case records the kind of defect and a note on what breaks.
The note is never shown to the pipeline or to Claude.

The slice uses TinyDB (MIT): ten commits that touch `tinydb/`, one defect each, covering a wrong
boolean operator, a dropped cache-key field, a resource leak, an off-by-one, a wrong null check,
the wrong exception type, a missing check, two swapped-argument bugs and an inverted condition.
A commit with a real indentation error of its own (7344e16) was left out.

**Materializing** (`prism/bench/cases.py`). One git worktree per case under
`<root>/worktrees/<case>`, reset to the commit and cleaned before the defect is planted, so a
rerun gives the same diff. Git runs with `core.autocrlf=false`: with Windows checkouts the files
otherwise have CRLF endings and multi-line anchors stop matching. Clones and worktrees live
outside the repo (`PRISM_BENCH_ROOT`, here on D:).

**Runner** (`prism bench run CASES --run NAME`). Each case is reviewed with `review_async` in
its own work dir under `<root>/runs/<run>/<case>`, with the in-memory store (the case's checkout
is indexed for context), nothing saved to Postgres and tracing off. The handoff verifier stops a
case while Claude's answers are missing, so the runner gathers every waiting case's open
requests into one folder, `<run>/handoff/round_NN`, with ids prefixed `<case>::`, and the usual
rubrics. One Claude session (a Sonnet subagent, as in every live check so far) answers the
whole round; the next run hands each answer back to its case and reviews again. Reviewer
replies are cached, so a rerun calls no local model. A round that is unchanged and unanswered
is reused rather than written again.

**Metric** (`prism/bench/score.py`). A case is a hit when a posted comment is on the defect's
file within 3 lines of the defect's line. Recall is hits over cases. Each case also records the
posted comments, how many comments the verifier considered, which model wrote the hit, whether a
comment near the defect was written and then dropped (a verifier miss rather than a reviewer
miss), and the first run's wall time (reviewer, SAST and in-memory indexing; the reruns are
cache hits and are not counted). Results go to `results.jsonl` and `summary.json` in the run.

## Consequences

* Recall at ±3 lines credits a comment for being in the right place, not for naming the bug. A
  comment next to the defect about something else still counts. The full benchmark adds a judge
  for that (PLAN.md's precision judge), also answered by a Sonnet subagent.
* Comments per PR is reported, but with no clean PRs yet the slice says nothing about false
  positives. The full benchmark adds 30 clean PRs.
* Ten cases on one repo is a smoke test of the harness, not a result. The numbers are read with
  that in mind.
