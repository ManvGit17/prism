# ADR 0011: The verifier is a Claude model, run as a batch handoff

Date: 2026-11-14. Status: accepted.

## Context

`prism-reviewer:v2` ties with the base model in the blinded judge (`eval/reports/sft.md`), but a
lot of what it writes is wrong, and it sounds sure either way. The rule checks (ADR 0010) catch
comments on the wrong line, vague ones and repeats. They cannot tell whether a claim is true.
PLAN.md asks the verifier to check each claim against the code and the retrieved context, and to
assign severity and category.

A 3B model checking another 3B model's claims did not seem worth building, so the verifier is a
Claude model (Sonnet or Opus). The build machine has no API key. Phase 4's judge already ran
Claude this way: a script wrote batches, a Claude session wrote verdicts, and a second script read
them back.

## Decision

**Two runs of one command.** `prism review --work DIR`:

1. Plans the PR, calls the reviewer, applies the rule checks, retrieves context for the hunks
   that still have a comment, and writes `DIR/verify/requests_NN.jsonl` (25 per file) and
   `DIR/verify/RUBRIC.md`. It exits with code 2 and says how many verdicts are needed.
2. A Claude model reads the rubric and writes `verdicts_NN.jsonl`, one line per request.
3. The same command again reads the verdicts and prints the `PRReview`.

Reviewer outcomes are cached per hunk in `DIR/replies.json` (keyed by path and a hash of the
hunk), including hunks that failed or ran out of time, so the second run calls no model and
plans the same hunks. The default work directory is
`PRISM_REVIEW_WORK_DIR/<repo>-<head sha>-<diff hash>`, so rerunning the same command finds it.

**What a request holds.** The id (`path#hunk index`), the line, the comment, the hunk with its
new-file line numbers, the rendered context (ADR 0007) and the planner's risk reasons. The
comment and hunk sit inside an `<untrusted_pull_request>` tag that their text cannot close, and
the rubric says to ignore instructions in them.

**What a verdict holds.** `keep`, `severity`, `category`, `confidence` and `evidence`. A comment is
posted only if the verdict keeps it, its confidence is at least `PRISM_VERIFIER_MIN_CONFIDENCE`
(0.6), and, for a security comment, it cites evidence. Lines that do not parse are reported and
their requests stay pending.

**The rubric asks for truth first.** A wrong claim is dropped even if it is specific. A true but
weak comment is kept with low confidence, so the threshold decides, not the verifier's mood.

`PRISM_VERIFIER=rules` keeps the stage 1 behaviour: no model check, keyword labels.

## Consequences

- A review is not one command any more: someone has to run the verifier between the two runs.
  For this project that is fine, because the reviews it runs are evaluations (Phase 7), and the
  same handoff is how they are judged. For a GitHub App it is not: stage 4 adds an API backend
  behind the same verifier interface, used when a key is set.
- The verifier sees only the request, not the repository. That keeps it reproducible and makes
  the context the thing being tested, which is what Phase 7's ablation measures.
- Who wrote the verdicts (Sonnet or Opus, which session) is not recorded in the review. The
  summary says only `claude (handoff)`.
