# ADR 0003: Qwen2.5-Coder 3B as the base reviewer model

Date: 2026-11-11. Status: accepted for the pipeline work, open for fine-tuning.

## Context

Phases 1, 2 and 5 are built against an untuned base model, so the base model has to give usable
review comments on its own. The candidates were `qwen2.5-coder:1.5b` and `qwen2.5-coder:3b`, both
served by Ollama on CPU.

## Decision

Use `qwen2.5-coder:3b` as the default (`PRISM_REVIEWER_MODEL`).

A quick manual check on a handful of hunks with planted bugs showed the untuned 1.5B mostly
describing the code rather than reviewing it, while the 3B named some of the bugs. This was an
informal check, not an evaluation, so no numbers from it are quoted here. The measured comparison
belongs to Phase 7.

## Consequences

- The 3B takes 2.2 GB loaded against 1.2 GB, and is noticeably slower per hunk on this CPU. The
  review budget and concurrency limit in Phase 5 have to allow for that.
- **Licence.** The 3B is released under the Qwen research licence, which is non-commercial. The
  1.5B is Apache 2.0. Check the licence terms before publishing tuned 3B weights.
- Which size to fine-tune is not decided here. A tuned 1.5B may close the gap at a lower cost per
  hunk, so Phase 4 should compare both if Colab time allows.
- Neither model was reliable on line numbers in the check. Phase 5 should number the lines in the
  prompt.
