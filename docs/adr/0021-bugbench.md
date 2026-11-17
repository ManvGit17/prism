# ADR 0021: BugBench, real pull requests with known bugs

Status: accepted.

## Context

To measure a reviewer I need pull requests where I know there is a bug and where it is, plus
pull requests with no known bug to count false positives. Existing review datasets give a hunk
and a human comment, not a defect at a known line, so I built my own benchmark from real
commits of open-source repositories.

## Decision

**Repositories** (`bugbench/repos.toml`): TinyDB (MIT), aiosqlite (MIT, async code, so missing
`await` is a real risk), sqlite-utils (Apache-2.0, builds SQL) and jsoup (MIT, Java). Each has
a test command and its own test environment.

**Three kinds of case** (`bugbench/cases/*.toml`). Every case is a real commit, reviewed as a
pull request against its parent, so the defect sits inside a real change with its tests and
docs, not alone in a one-line diff.

* **Planted** (139). One defect is planted in a line the commit added: off-by-one, wrong null
  check, wrong boolean operator, inverted condition, missing `await`, wrong exception type,
  swapped arguments, wrong return value, a dropped `close()`. `prism bench generate` proposes
  mutations and keeps one only if the repo's tests pass at the commit and a named test fails with
  it, so every planted bug is a real behaviour change. Mutants that do not parse or compile are
  dropped, and no kind takes more than about a fifth of a repo's cases. Three SQL injections in
  sqlite-utils are written by hand.
* **Real** (30). A commit that introduced a bug a later commit fixed. `prism bench real` finds
  candidates with SZZ (blame the lines a fix changed back to the commit that wrote them), and
  each candidate is checked by hand: the fix must change behaviour, and the blamed lines must be
  the faulty code added by that commit. 10 came from a first, smaller search; in the second, 32
  of 208 candidates passed and I took 20 of them, spread across the repos.
* **Clean** (29). Commits whose tests pass and that got no planted bug.

One commit is one case only; the loader refuses two cases on the same commit.

**Running** (`prism bench run`, `prism bench all`). Each case is checked out in its own git
worktree with the defect planted, and reviewed by the normal pipeline with the repo indexed in
memory. Claude's work goes through the same handoff as everywhere else: the runner gathers the
open requests of every case into one round folder with a neutral README (it says nothing about
whether a PR has a bug), Sonnet subagents answer it, and the next pass hands the answers back.
Local replies and Claude's answers are cached per case, so a pass after the first only reviews
the cases still waiting, and a run can be seeded from an earlier one that asked the same
questions. `bugbench/configs.toml` lists the configurations and `make eval-bugbench` runs them
all into `eval/reports/bugbench.md`.

**Scoring** (`prism/bench/score.py`, `prism/bench/judge.py`).

* A planted or real bug is **found** when a posted comment is on the defect's file within 3 lines
  of the defect.
* A clean PR is **flagged** when it gets any posted comment.
* Every posted comment is also shown to a judge (Claude, with the hunk and, for a buggy PR, the
  defect): is it a true, useful review comment (**precision**), and does it name the defect,
  wherever it is placed?

## Results

Report: `eval/reports/bugbench.md`. On all 198 pull requests:

| | PRism | PRism, whole PR to Claude |
|---|---|---|
| Planted bugs found | 115/139 | 124/139 |
| Real bugs found | 17/30 | 18/30 |
| Clean PRs with any comment | 0/29 | 1/29 |
| Comments per buggy PR | 0.99 | 1.02 |

The judge rated 162 of the 167 comments PRism posted (0.97) as true and useful, and found a
comment naming the defect in 117 of the 139 planted-bug PRs and 17 of the 30 real ones. 23 of
PRism's planted-bug finds came straight from the fine-tuned local reviewer.

## Consequences

* Planted bugs are test-checked, so they are bugs a test catches, and the reviewer sees the
  commit's own tests in the PR. Real bugs have no such filter, which is one reason they are
  harder; I report the two apart.
* Many old commits cannot run their tests in today's environment, so planted cases lean towards
  recent history; aiosqlite has fewer cases than the others for that reason.
* The ablations in ADRs 0023 to 0026 were measured on the first 83 pull requests, before the
  benchmark grew to 198.
