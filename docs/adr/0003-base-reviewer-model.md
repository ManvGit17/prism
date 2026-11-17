# ADR 0003: Qwen2.5-Coder 3B as the base reviewer model

Status: accepted. The default reviewer is now the fine-tuned `prism-reviewer:v2` (ADR 0009); this
model stays a supported setting.

## Context

The retrieval, prompting and verification pipeline is built against an untuned base model, so the
base model has to give usable review comments on its own. The candidates were `qwen2.5-coder:1.5b`
and `qwen2.5-coder:3b`, both served by Ollama on CPU.

## Decision

Use `qwen2.5-coder:3b` as the base default (`PRISM_REVIEWER_MODEL`).

A quick manual check on a handful of hunks with planted bugs showed the untuned 1.5B mostly
describing the code rather than reviewing it, while the 3B named some of the bugs. This was an
informal check, not an evaluation, so no numbers from it are quoted here. The measured comparison
is done in the evaluation.

## Consequences

- The 3B takes 2.2 GB loaded against 1.2 GB, and is noticeably slower per hunk on this CPU. The
  review budget and concurrency limit have to allow for that.
- **Licence.** The 3B is released under the Qwen research licence, which is non-commercial. The
  1.5B is Apache 2.0. I check the licence terms before publishing tuned 3B weights.
- Which size to fine-tune is a separate question. A tuned 1.5B may close the gap at a lower cost
  per hunk, so the two are worth comparing when training time allows.
- Neither model was reliable on line numbers in the check, so the reviewer only writes the
  comment and PRism places it on a line itself (ADR 0009); Claude's requests number every line.
