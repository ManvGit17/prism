# ADR 0015: Reviews are stored with every decision

Date: 2026-11-14. Status: accepted.

## Context

Phase 6 adds three readers of past reviews: the MCP tools `explain_finding` and `review_history`,
the GitHub App (which posts a review after its verification is answered, ADR 0011) and the
dashboard. Until now a review existed only as the JSON `prism review` printed.

## Decision

Two tables in the Compose Postgres (migration 2): `reviews` and `review_findings`. A finding is
every comment the pipeline considered, from the local model, a SAST tool or Claude, with whether
it was posted, the decision ("kept" or why it was dropped), the verifier's own words, severity,
category, confidence, evidence, the suggested fix, the hunk and the SAST findings in that hunk.
Dropped comments are kept on purpose: "why did PRism say nothing about this line?" is a question
`explain_finding` should answer, and the dashboard's accept rate needs them.

A review is keyed by `repo#pr#head_sha#diff_hash` (`local` for a diff with no PR). Saving the same
key replaces the review and its findings, so a handoff review that stops as
`awaiting_verification` and then finishes as `complete` ends as one row. That key is also the
queue's idempotency key in a later stage.

`prism review` saves by default (`PRISM_REVIEW_STORE=postgres`, `--no-save`); when Postgres is
down it prints a note and the review still finishes. `InMemoryReviewStore` and
`PostgresReviewStore` pass the same contract tests.

## Consequences

- A finding row holds its hunk text, so one review of a big PR stores the diff again. That is
  fine at this size and keeps `explain_finding` to one lookup.
- `github_comment_id` is empty until the App posts; the feedback loop fills it in.
