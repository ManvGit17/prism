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
is reused rather than written again. Each round folder has a `README.md` the runner writes: which
request file goes with which rubric and answer file, and that only the folder may be read. It
says nothing about whether the pull requests have bugs, so a subagent's prompt is just "follow
README.md", the same for every round and, later, for clean PRs.

**Moving a comment.** The first run missed one defect because the local reviewer named the bug
correctly 13 lines above it, and the verifier could only say so in its reason. A verdict now has
an optional `line`: where a kept comment belongs. It is applied only if the hunk shows that line
(an added or context line), so the verifier cannot move a comment off the diff.

**Metric** (`prism/bench/score.py`). A case is a hit when a posted comment is on the defect's
file within 3 lines of the defect's line. Recall is hits over cases. Each case also records the
posted comments, how many comments the verifier considered, which model wrote the hit, whether a
comment near the defect was written and then dropped (a verifier miss rather than a reviewer
miss), and the first run's wall time (reviewer, SAST and in-memory indexing; the reruns are
cache hits and are not counted). Results go to `results.jsonl` and `summary.json` in the run.

## The mid-size benchmark (2026-11-15)

**Groups.** A case is `injected` (a planted defect), `real` (a commit that introduced a bug a
later commit fixed; `lines` are the lines the fix changed, at the introducing commit) or `clean`
(the commit as it is). Injected and real recall are reported apart, as PLAN.md asks, and clean
PRs get a false-positive rate: the share of clean PRs with at least one posted comment.

**Repos** (`bugbench/repos.toml`): TinyDB (MIT), aiosqlite (MIT, async, for missing `await`),
sqlite-utils (Apache-2.0, builds SQL) and jsoup (MIT, Java). Each has a test command and a venv
or Maven cache on D.

**Injected cases are test-checked** (`prism bench generate`, `prism/bench/mutate.py`). On the
source lines a recent commit added, single-line operators propose a defect: off-by-one, wrong
null check, wrong boolean operator, inverted condition, missing `await`, wrong exception type,
swapped arguments, wrong return value, a dropped `close()`. A mutation is kept only if the
repo's tests pass at the commit and fail with it, and it names the failing test (a hang counts,
recorded as `timeout`; a bare exit code does not). Mutations that do not parse or compile are
dropped, since a build error is not a review finding. Operators skip prose (docstrings, block
comments), lines that test the interpreter (`sys.version_info`), and shapes nobody writes by
mistake (`not a is b`, `if (!(i == -1))`, `while not True`). One case per commit; no kind takes
more than about a fifth of a repo's set. Commits whose tests pass and that got no case become
the clean cases. A timed-out test run is killed with its whole process tree: Maven's forked JVM
holds the output pipes, and killing only the parent left the generator waiting for an hour.

**Real bugs** (`prism/bench/realbugs.py`) come from SZZ: a fix commit's removed or changed
source lines are blamed at its parent to find the commit that wrote them. Fixes to docs, typos,
style, type hints or tooling are skipped, and so are blamed lines in comments. The output is a
list of candidates: I read each fix and the blamed line at the introducing commit and kept 10
whose fix changes behaviour and whose blamed line is the buggy code.

**Security cases** are hand-written: three SQL injections in sqlite-utils, where a bound
parameter is pasted into the SQL text. No test checks them. PLAN.md's hardcoded-secret kind is
left out because it has no natural place in these libraries.

**Result (run `mid-01`, eval/reports/bugbench_mid.md)**: injected recall 48/57 (0.84), real-bug
recall 6/10 (0.60), no comment on any of the 16 clean PRs, 1.05 comments per injected PR, first
run p50 20 s and p95 90 s. Claude wrote 41 of the 48 injected hits, almost all through
escalation.

## Consequences

* Recall at ±3 lines credits a comment for being in the right place, not for naming the bug. A
  comment next to the defect about something else still counts. The full benchmark adds a judge
  for that (PLAN.md's precision judge), also answered by a Sonnet subagent.
* Comments per PR is reported, but with no clean PRs yet the slice says nothing about false
  positives. The full benchmark adds 30 clean PRs.
* Ten cases on one repo is a smoke test of the harness, not a result. The numbers are read with
  that in mind.
* The same requests answered twice gave different verdicts in places (which model gets credit
  for a hit changed in two cases), so a single run's per-case detail is noisy. The full benchmark
  should report the spread over more than one answer pass where it matters.
* The rubric change reaches Langfuse only after `prism prompts push`; with
  `PRISM_PROMPT_SOURCE=langfuse` the old verifier prompt is used until then.
* Test-checked mutants are a biased sample: only defects a test catches get in, and the
  reviewer sees the commit's own tests in the PR. Real bugs had no such filter, which is one
  reason their recall is lower.
* Many old commits cannot run their tests in today's environment (newer pytest and Python, a
  test layout that moved), so the cases lean towards recent history. aiosqlite stopped at 11
  injected (10 after one was dropped) for that reason.
* A clean false-positive rate of 0 on 16 PRs says the verifier is strict, not that the pipeline
  never errs. The verifier dropped every comment it saw on clean PRs (0 to 15 per PR), and it
  drops most comments on injected PRs too. Recall then depends on escalation catching what the
  local model missed.
