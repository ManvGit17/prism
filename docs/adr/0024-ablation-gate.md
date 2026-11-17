# ADR 0024: Ablation, the gate off

Status: accepted.

This ablation was measured on the first 83 pull requests of BugBench (57 planted bugs, 10 real
bugs, 16 clean PRs), before the benchmark was grown.

## Context

The Claude-only ablation (ADR 0023) found that 3 of its misses were hunks that never reached a
model: the CodeBERT gate (ADR 0008) skipped them, or the 40-hunk budget did. The question is what
the gate saves against what it costs in recall; a small saving is a valid answer.

## Decision

Run PRism as in the main run (`mid-01`) with `PRISM_GATE=pass-all`, as run `gate-off-01`. The run
was seeded from a copy of `mid-01`: the local model's cached replies and Claude's answers are
reused wherever the request is the same, so only the hunks the gate used to skip were reviewed
and only their comments were sent to Claude. Wall-clock time on a rerun from cache says nothing,
so the saving is counted in work: hunks sent to the local model, and requests sent to Claude.

I found that Bandit was missing from the virtualenv when `mid-01` ran and present for
`gate-off-01`, so this run also had 3 Bandit findings in sqlite-utils that `mid-01` did not. It
did not change any posted comment (see ADR 0026).

## Results

Report: `eval/reports/bugbench_ablation_gate.md`.

| | Gate on | Gate off |
|---|---|---|
| Injected recall | 48/57 | 50/57 |
| Real-bug recall | 6/10 | 6/10 |
| Clean PRs with any comment | 0/16 | 0/16 |
| Comments per buggy PR | 1.00 | 1.06 |
| Hunks sent to the local model | 425 | 600 |
| Claude requests | 519 | 686 |

The two extra hits (aiosqlite-i11, sqlite-utils-i04) are both hunks the gate scored below its
threshold. Nothing found with the gate on was lost with it off, and no clean PR got a comment.

## Consequences

* The gate does what it was built for: it skips 29% of the hunks and saves 24% of the Claude
  requests. Its cost is 2 of 57 planted bugs (3.5 points of recall) on this benchmark, and none
  of the real bugs.
* Whether that trade is right depends on how PRism runs. With a hosted model and a budget, keep
  it; for a local model with time to spare, the 2 extra bugs may be worth more than the 41% more
  local work. The default stays on, and the setting is documented.
* The third unreviewed hunk from ADR 0023 (sqlite-utils-i11) is still missed with the gate off:
  that PR touches 11 files and the 40-hunk budget, not the gate, leaves the defect out.
