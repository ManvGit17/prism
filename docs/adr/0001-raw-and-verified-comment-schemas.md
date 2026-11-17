# ADR 0001: Separate schemas for model output and verified comments

Status: accepted.

## Context

The reviewer model is fine-tuned on CodeReviewer comment generation, which pairs a diff hunk with
a comment. The data has no labels for severity or category. A posted comment needs both.

## Decision

Two schemas in `prism/schemas.py`:

- `RawComment` (`line`, `comment`) is the only shape a reviewer model may emit. Extra fields are
  rejected, so a model that invents a severity fails validation.
- `ReviewComment` adds `path`, `severity`, `category`, `confidence`, `model` and `evidence`. Only
  the verifier creates it. `suggested_fix` is optional and comes only from the frontier path.

Both are frozen and forbid unknown fields.

## Consequences

- The tuned model is served in the format it was trained on.
- A comment cannot be posted without passing the verifier, because nothing else produces a
  `ReviewComment`.
- The dataset holds no more than a hunk and a comment (see `docs/datasets.md`). It also has no
  line labels, so the model writes the comment only and `RawComment.line` is filled by
  `anchor_line` (ADR 0009).
