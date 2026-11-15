# ADR 0022: PR-Agent as the baseline, with Claude through a handoff

Date: 2026-11-15. Status: accepted.

## Context

PLAN.md Phase 7 asks for an existing open-source reviewer run on the same benchmark, because a
number with no baseline says little. Qodo's PR-Agent is open source (MIT), maintained, and the
best-known tool of its kind. Every Claude answer in this project comes from a Sonnet subagent
through a handoff (no API key), so the baseline has to work the same way to be comparable.

## Decision

**Version and isolation.** PR-Agent 0.49.0, the newest release at least two weeks old, in its
own venv on D (`<bench root>/envs/pr-agent`). It is not a PRism dependency.

**How it sees a PR.** Its local git provider, as a user would run it on a branch: the case's
worktree is checked out on a branch named after the commit's subject (that is the PR title it
sees), the planted defect is amended into the commit with fixed dates so the prompt is the same
on every run, and `--pr_url` names a base branch at the commit's parent. The PR description is
the commit message. It gets the full files, not only the diff. Default settings otherwise.

**Which tools.** `review` (its key issues, each with a file and line range) and `improve` (code
suggestions with a line range, after its self-reflection pass). These are the two of the three
tools its GitHub App runs on a new PR that point at code; `describe` only summarizes.

**The model.** `prism.bench.llm_handoff` is an OpenAI-compatible endpoint on 127.0.0.1. A prompt
it has an answer for gets that answer; a new prompt is saved as pending and the call fails with
HTTP 400, which PR-Agent does not retry. The pending prompts go to `rounds/round_NN` in batches
of 8 with a neutral README: answer each request as the model behind the API would. Sonnet
subagents answer them, the next run serves the answers, and PR-Agent proceeds to its next call
(the self-reflection of `improve` is a second round). Prompts are keyed by a hash of their
messages without the "Today's Date" line, so a round that spans midnight still matches.

**Scoring.** A finding hits when it is on the defect's file and its line range comes within 3
lines of a defect line: the same tolerance as PRism's single-line comments. The report also
gives recall counting only findings of at most 10 lines, so wide ranges cannot buy hits.

## Result (runs `mid-01` and `pra-01`, eval/reports/bugbench_vs_pr_agent.md)

| | PRism | PR-Agent |
|---|---|---|
| Injected recall | 48/57 | 54/57 |
| Real-bug recall | 6/10 | 8/10 |
| Clean PRs with any comment | 0/16 | 2/16 |
| Comments per buggy PR | 1.00 | 2.03 |
| Claude requests / input | 519 / 2.9 MB | 227 / 3.8 MB |

PR-Agent found every defect PRism found and 8 more. Its two clean-PR findings read as true
issues in the original commits, which the benchmark still counts as false positives.

## Consequences

* The honest reading: with the same frontier model behind both, a tool that shows Claude every
  hunk finds more than one that shows Claude mostly the hunks a small local model and the
  verifier pick. PRism's routing is the reason for the gap: in its own run, Claude wrote 41 of 48
  hits, and the misses are where nothing escalated the hunk.
* PRism's advantages here are narrow: fewer comments per PR, none on clean PRs, and about a
  quarter less Claude input. That is a precision-cost trade, not a win, and the ablations should
  show what each part of PRism buys.
* Time is not compared. Neither run times Claude (a handoff), and PR-Agent's own processing on
  cached answers is not a fair stand-in for a hosted model call.
* One answer pass per system. Run-to-run variation (seen in the slice) is not measured yet.
