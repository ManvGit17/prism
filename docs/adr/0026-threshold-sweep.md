# ADR 0026: The confidence threshold sweep

Status: accepted.

This sweep was measured on the first 83 pull requests of BugBench (57 planted bugs, 10 real bugs,
16 clean PRs), before the benchmark was grown.

## Context

`PRISM_VERIFIER_MIN_CONFIDENCE` (0.6) decides which of Claude's verdicts and comments are
posted. The Claude-only and whole-PR ablations (ADRs 0023, 0025) both found that many misses were
bugs Claude named below 0.6, and re-scoring at 0.3 changed the picture. The threshold deserves an
ablation of its own.

The threshold has a second job in PRism: a local comment Claude keeps below it sends its hunk
back to Claude for a review of its own (ADR 0013). So a new threshold can ask Claude new
questions, and a re-score is free only where it does not.

## Decision

`prism bench sweep CASES --run RUN` reviews each case of a finished run once more from its
caches, rebuilds the batch the verifier was given from the final state, and then answers that
batch at each threshold from the answers already saved, writing nothing. At a threshold where
PRism would ask Claude something the run never asked, the PR is reported as unscored.
`prism bench sweep-table` puts runs side by side. Thresholds 0.3 to 0.9 in steps of 0.1, for the
main run (`mid-01`), Claude-only and whole-PR. No new Claude work.

Each sweep reproduces its run's own result at 0.6, case by case, and the earlier 0.3 re-runs
(`claude-only-01-conf03`, `whole-pr-01-conf03`) at 0.3.

I found that Bandit was missing from the virtualenv when `mid-01` and `claude-only-01` ran; it
did not change their posted comments. Those two were swept with
`PRISM_SAST_TOOLS=semgrep,ruff,pmd` to match. The bench summary records which SAST tools
actually ran (`sast_ran`), so a missing tool shows up.

## Results

Report: `eval/reports/bugbench_threshold_sweep.md`.

Injected recall / real-bug recall / clean PRs with any comment:

| Threshold | PRism | Claude-only | Whole PR |
|---|---|---|---|
| 0.3 | 50/57, 7/10, 1/16 | 51/57, 7/10, 3/16 | 52/57, 9/10, 2/16 |
| 0.4 | 50/57, 7/10, 0/16 | 50/57, 7/10, 3/16 | 52/57, 9/10, 2/16 |
| 0.5 | 49/57, 7/10, 0/16 | 49/57, 7/10, 1/16 | 52/57, 6/10, 1/16 |
| 0.6 | 48/57, 6/10, 0/16 | 49/57, 6/10, 0/16 | 52/57, 5/10, 0/16 |
| 0.7 | 46/55, 4/9, 0/16 * | 47/57, 5/10, 0/16 | 52/57, 5/10, 0/16 |
| 0.8 | 44/54, 3/9, 0/16 * | 46/57, 3/10, 0/16 | 49/57, 4/10, 0/16 |
| 0.9 | 38/53, 2/9, 0/16 * | 38/57, 0/10, 0/16 | 43/57, 1/10, 0/16 |

\* 3, 4 and 5 PRs unscored: they would need new Claude reviews.

* **PRism at 0.4 beats 0.6 at no cost on clean PRs.** It finds 50/57 and 7/10 against 48/57 and
  6/10. The three gains (jsoup-i01, sqlite-utils-i06, sqlite-utils-r01) are bugs Claude named at
  0.4 to 0.5. Nothing is lost, and no clean PR gets a comment until 0.3 (a PMD unused-variable
  note). Comments per buggy PR go from 1.00 to 1.18; whether the extra ones are useful is not
  measured here.
* **The whole-PR view is the most stable.** It holds 52/57 from 0.3 to 0.7. Its real-bug recall
  is where the threshold bites: 9/10 at 0.4, 5/10 at 0.6.
* **Above 0.6 every run loses bugs fast,** and no clean PR is flagged at 0.6 or above anyway, so
  there is nothing to buy up there.
* Claude-only flags 3 clean PRs at 0.4, where PRism flags none. A likely reason, not checked:
  with the local model first, Claude mostly judges comments rather than writing its own, and
  its own comments are the speculative ones.

## Consequences

* On this benchmark, 0.4 is a better default for PRism than 0.6. I have not changed the default:
  16 clean PRs is too few to set a false-positive rate, and the threshold was chosen on the same
  cases it is measured on. It should be confirmed when the set grows to 150/30/30, with a judge
  for whether the extra comments are useful.
* The threshold and the view interact: the whole-PR view at 0.4 is the best configuration
  measured so far (52/57, 9/10, 2/16 clean), against PR-Agent's 54/57, 8/10 and 2/16.
* A sweep is cheap only because answers are cached by what was asked. Sweeping above the run's
  threshold in PRism needs new Claude work, and the sweep says how much.
