# The review pipeline on real diffs

Phase 5. What `prism review` did on two diffs while it was being built, run by run. These are
spot checks of the pipeline, not a benchmark: the 10-PR slice and the numbers that compare
settings come in Phase 7.

Setup for every run: `prism-reviewer:v2` on the host Ollama at 4 parallel requests, the ONNX gate,
graph context from an in-memory index of the checkout, Bandit, Semgrep (OWASP rules) and Ruff,
and Claude Sonnet as the verifier through the file handoff (ADR 0011). The verdicts each Sonnet
session wrote are not in the repo; the counts and the comments below are.

## A commit with planted bugs

A two-file commit to a small test repo (`shop/users.py`, `shop/backup.py`) that adds five
problems on purpose: an SQL query built with an f-string, `subprocess.call(..., shell=True)` with a
path in the command, `pickle.loads` on caller data, unsalted MD5 for passwords, and a pagination
off-by-one where the new docstring says pages count from 1 and the code does not.

| Stage | What came out | Problems found (of 5) |
|---|---|---|
| Rules only (stage 1) | 2 comments from the tuned model, neither about any of the five | 0 |
| + Claude verifier (stage 2) | both dropped | 0 |
| + SAST (stage 3) | 4 tool comments kept, both model comments dropped | 4 |
| + routing to Claude (stage 4) | 5 comments, all correct, 4 with a suggested fix | 5 |

- The tuned model wrote "I think we should use `subprocess.run` instead of `call`" on the
  `shell=True` line and "I think this should be a separate PR" on the docstring. Neither names a
  problem. The verifier dropped both.
- Bandit found the four security problems; Semgrep found two of them again. Only the strongest
  finding per line became a comment (ADR 0012).
- Nothing found the off-by-one until stage 4. Both hunks touch security-sensitive code, so the
  router sent them to Claude instead of the local model, and Claude's review of the
  `users.py` hunk named it: "the new docstring says pages count from 1, but start =
  number * size", fix `start = (number - 1) * size`.
- With routing, the final review had 5 comments: SQL injection (critical), command injection
  (critical), pickle (critical), MD5 for passwords (major), off-by-one (major). Risk verdict high.

## A real commit: c8ac930 of this repo

The Python part of commit c8ac930 (length-grouped batches and checkpoints in the Phase 4
training code): 9 hunks in 5 files, no planted bugs.

| Run | Result |
|---|---|
| Rules only | the gate skipped 1 hunk; 8 model comments, all kept |
| Claude verifier, no SAST or routing (stage 2) | all 8 dropped; 0 comments |

| Everything on (the Phase 5 done-when run) | all 8 dropped again; 0 comments, risk low |

The verifier's reasons for the two drops checked by hand were right: one comment asked for
`args.scratch` on a line that already passes it, another wanted a `__main__` guard around a
warning that is inside a function, not at import time.

**The done-when run.** The full default path from a clean work directory: routing on (no hunk
here looks security-sensitive or has 60 changed lines, so all 8 went to the local model), SAST,
graph context, prompts read from Langfuse (`production`, v1), tracing on. The first run stopped for
8 verdicts; a fresh Sonnet session dropped all 8 and escalated none; the second run printed a
`PRReview` that validates against the schema (verdict low, 0 comments, 13.7 s from the cached
replies). Bandit reported 29 findings in the changed files, almost all `assert` in test files,
all low severity, so none became a comment. The first run's trace has 19 observations: the
`review` chain, the five `file` spans and the `sast` tool starting within 1 ms of each other, 8
generations under their files, and the context, verifier and aggregator steps, tagged `repo:prism`,
`pr:5`, `model:prism-reviewer:v2` and `verifier:claude (handoff)`.

## What this says

- On these two diffs the tuned 3B model wrote 10 comments and none of them was worth posting.
  Without the verifier all 10 would have gone out.
- Static analysis found every security problem in the planted commit at no model cost.
- The one logic bug was found only by the frontier model reading the hunk. Whether routing finds
  enough to pay for itself on real PRs is a Phase 7 question.
