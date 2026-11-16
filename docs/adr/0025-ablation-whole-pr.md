# ADR 0025: Ablation, the whole-PR view

Date: 2026-11-16. Status: accepted.

## Context

In the Claude-only ablation (ADR 0023), 5 of the 12 misses were hunks Claude read and said
nothing about, and 3 more were bugs it named below the 0.6 threshold. The obvious difference
from PR-Agent, which found 54 of 57, is how the change is shown: PR-Agent puts the whole PR in
one prompt, and PRism shows Claude one hunk per request, with the code retrieved for it.

## Decision

`PRISM_REVIEW_UNIT=pr`, used with `PRISM_ROUTE_ALL=true`, puts every hunk routed up front into one
request per PR, grouped by file, each hunk with the same retrieved context and tool findings as
before. A new rubric (`PR_RUBRIC.md`) asks Claude to read the whole request before writing, and
each comment names its `path` as well as its line. Comments are then placed on the hunk that
shows their line and go through the same checks as before: line in the diff, not vague, not a
duplicate, at most 3 per hunk, the confidence threshold and the evidence rule for security. A
comment on a file or line the PR does not show is dropped.

Everything else is as in `claude-only-01`: the gate, the 40-hunk budget, SAST, graph context and
the 0.6 threshold. The run (`whole-pr-01`) was seeded from a copy of `claude-only-01`, so the SAST
verdicts were reused. Bandit raised 3 SQL-string findings in sqlite-utils that it had not raised
in that run (it did in `gate-off-01`), so those 3 were verified fresh. Bandit's output is not
identical from run to run on this machine, and that is worth knowing for every comparison here.
As for ADR 0023, the same answers were re-scored at confidence 0.3 (`whole-pr-01-conf03`).

## Result (eval/reports/bugbench_ablation_whole_pr.md)

| | Claude-only | Whole PR | Claude-only, conf 0.3 | Whole PR, conf 0.3 | PR-Agent |
|---|---|---|---|---|---|
| Injected recall | 49/57 | 52/57 | 51/57 | 52/57 | 54/57 |
| Real-bug recall | 6/10 | 5/10 | 7/10 | 9/10 | 8/10 |
| Clean PRs with any comment | 0/16 | 0/16 | 3/16 | 2/16 | 2/16 |
| Comments per buggy PR | 0.99 | 1.00 | 1.34 | 1.40 | 2.03 |

The same 2.29 MB of hunks and context went to Claude in both runs, as 450 requests per hunk or
82 per PR. Claude wrote about the same number of comments (91 and 92), and 66 and 67 were posted.
The whole-PR view did not make Claude say more. It made what it said land on the bug more often:

* 3 more planted bugs, with none lost. In jsoup-i12 the per-hunk answer named the off-by-one at
  confidence 0.35; with the javadoc and the test in view it was 0.75. In sqlite-utils-i03 the
  clue was a new test in another file. In tinydb-i11 the per-hunk answer said nothing.
* Real bugs moved both ways. Two were found that the per-hunk run missed (sqlite-utils-r02, again
  from 0.5 to 0.75, and tinydb-r01), and three were lost. In all three the bug was named, but at
  0.35 to 0.5.
* At 0.3, the whole-PR view finds 9 of 10 real bugs, one more than PR-Agent, and comments on 2
  clean PRs, the same number as PR-Agent, with about two thirds of its comments.

## Consequences

* How Claude sees the change matters more than which model reviews first. The whole-PR view is
  the first change in these ablations that moves recall by more than a case or two at no extra
  cost in comments.
* Confidence is the other lever. In both views most of the remaining real-bug misses are named
  below 0.6, and at 0.3 the false-positive cost on clean PRs is 2 of 16. A threshold ablation
  across the views is the natural next step.
* The 5 planted bugs still missed at 0.3: 2 are hunks the gate skips (found with the gate off,
  ADR 0024), 1 is left out by the 40-hunk budget, and in 2 the defect's file was in the request
  and Claude wrote nothing.
* This is not yet a production mode. One request per PR grows with the PR: the biggest request
  here was 145 KB (an 11-file PR), and only the 40-hunk budget bounds it. For production use it needs a size
  limit and a way to split a PR by file, both still to do. The default stays per hunk.
