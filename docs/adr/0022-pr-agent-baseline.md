# ADR 0022: PR-Agent as the baseline, with Claude through a handoff

Status: accepted.

This comparison was run on the first 83 pull requests of BugBench (57 planted bugs, 10 real
bugs, 16 clean PRs), before the benchmark was grown.

## Context

A number with no baseline says little, so I ran an existing open-source reviewer on the same
pull requests. Qodo's PR-Agent is open source (MIT), maintained, and the best-known tool of its
kind. Every Claude answer in PRism comes from a Sonnet subagent through a handoff, so the
baseline had to use the same model the same way to be comparable.

## Decision

**Version and isolation.** PR-Agent 0.49.0 in its own virtualenv, not a PRism dependency.

**How it sees a PR.** Its local git provider, as a user would run it on a branch: the case's
worktree is on a branch named after the commit's subject (the PR title it sees), the planted
defect is amended into the commit with fixed dates so the prompt is the same on every run, and
`--pr_url` names a base branch at the commit's parent. The PR description is the commit message.
It gets the full files, not only the diff, and default settings otherwise.

**Which tools.** `review` (key issues, each with a file and line range) and `improve` (code
suggestions with a line range, after its self-reflection pass): the two tools its GitHub App
runs on a new PR that point at code.

**The model.** `prism.bench.llm_handoff` is an OpenAI-compatible endpoint on 127.0.0.1. A prompt
it has an answer for gets that answer; a new prompt is saved as pending and the call fails, which
PR-Agent does not retry. Pending prompts go to a round folder with a neutral README, Sonnet
subagents answer them, and the next run serves the answers. Prompts are keyed by a hash of their
messages without the date line.

**Scoring.** A finding hits when it is on the defect's file and its line range comes within 3
lines of a defect line, the same tolerance as PRism's single-line comments.

## Results

Report: `eval/reports/bugbench_vs_pr_agent.md`.

| | PRism | PR-Agent |
|---|---|---|
| Planted bugs found | 48/57 | 54/57 |
| Real bugs found | 6/10 | 8/10 |
| Clean PRs with any comment | 0/16 | 2/16 |
| Comments per buggy PR | 1.00 | 2.03 |
| Claude requests / input | 519 / 2.9 MB | 227 / 3.8 MB |

## Consequences

* With the same model behind both, PR-Agent found more bugs here, and PRism posted half as many
  comments, put none on a clean PR, and sent Claude about a quarter less input.
* The comparison set the ablations: ADRs 0023 to 0026 test where the gap comes from. Replacing
  PRism's local reviewer with Claude changed little (ADR 0023). Showing Claude the whole pull
  request in one request instead of one hunk at a time closed most of it (ADR 0025), and at a
  confidence threshold of 0.4 that setup found 52/57 planted and 9/10 real bugs with 2 of 16
  clean PRs flagged, against PR-Agent's 54/57, 8/10 and 2/16 (ADR 0026).
* Time is not compared: neither run times Claude (a handoff), and PR-Agent's processing on
  cached answers is not a fair stand-in for a hosted model call.
