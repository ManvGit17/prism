# ADR 0023: Ablation, Claude reviews every hunk

Date: 2026-11-15. Status: accepted.

## Context

The PR-Agent baseline (ADR 0022) found 54 of 57 planted bugs to PRism's 48, with the same model
(Sonnet) behind both. The obvious suspect was PRism's local reviewer: Claude mostly sees what
`prism-reviewer:v2` said, and only looks at a hunk itself when the verifier escalates it. The
first ablation tests that directly.

## Decision

`PRISM_ROUTE_ALL=true` routes every planned hunk to Claude's review rubric up front, with the
reason `all`, and the local reviewer is not called. Everything else is PRism as it is: the
planner, the ONNX gate and the 40-hunk budget, SAST and its verification, graph context, the
rule checks and the 0.6 confidence threshold. The run is `claude-only-01`: 450 hunks reviewed
and 29 SAST comments verified, in one handoff round of 20 Sonnet subagents.

Because the answers are cached per case, the threshold can be re-scored for free:
`claude-only-01-conf03` is the same answers with `PRISM_VERIFIER_MIN_CONFIDENCE=0.3`.

## Result (eval/reports/bugbench_ablation_claude_only.md)

| | PRism | Claude-only | Claude-only, conf 0.3 | PR-Agent |
|---|---|---|---|---|
| Injected recall | 48/57 | 49/57 | 51/57 | 54/57 |
| Real-bug recall | 6/10 | 6/10 | 7/10 | 8/10 |
| Clean PRs with any comment | 0/16 | 0/16 | 3/16 | 2/16 |
| Comments per buggy PR | 1.00 | 0.99 | 1.34 | 2.03 |

Replacing the local reviewer with Claude changes almost nothing. Of the 12 Claude-only misses:

* 3: the defect's hunk never reached Claude (the gate or the hunk budget left it out).
* 3: Claude named the bug with confidence 0.35 to 0.5 and the 0.6 threshold dropped it.
* 1: Claude named the bug at the call site, 4 lines from the defect line.
* 5: Claude saw the hunk and said nothing. PR-Agent reads the whole PR in one prompt; PRism
  shows Claude one hunk at a time with retrieved context.

At 0.3 the three low-confidence finds post, and so do comments on 3 clean PRs: two are the same
real issues PR-Agent raised there, one is a PMD unused-variable note.

## Consequences

* The fine-tuned reviewer is not what costs PRism recall; the pipeline around the model is: what
  reaches it (gate, budget), how it sees the change (one hunk at a time), and how much it must be
  sure of to post. That is where the next changes go, and the remaining ablations (gate off,
  context modes, threshold) should be read in that light.
* It also means that, on this benchmark, the local reviewer is close to a free first pass: PRism
  with it is within one bug of Claude reviewing every hunk, while Claude reads less.
* The ±3 window undercounts call-site comments (one case here, three across all runs). A judge
  that asks "does this comment name the planted bug" would fix that; it is still to do.
