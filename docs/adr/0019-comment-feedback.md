# ADR 0019: Feedback on posted comments, into Langfuse

Date: 2026-11-15. Status: accepted.

## Context

PLAN.md Phase 6 asks for a feedback loop: a periodic task reads 👍/👎 reactions on PRism's
comments, writes them to Langfuse as scores on the matching trace and adds them to a dataset of
good and bad comments for a later DPO round. Polling must be bounded (comments from the last 14
days, conditional requests so unchanged comments cost no quota), and replies and resolved
threads arrive by webhook.

## Decision

**What is kept** (migration 4, `comment_feedback`): per posted finding, the human 👍 and 👎
counts, replies, whether its thread is resolved, the ETag of the last reactions response, when it
was checked, and what was last sent to Langfuse. Reviews now record their installation id, which
the reaction reads need. Reactions and replies from bots, PRism's own included, do not count.

**Reactions** (`prism.collect_feedback`, beat, every `PRISM_APP_FEEDBACK_INTERVAL_S` = 15 min;
`prism app feedback` for one pass). For each comment of a review posted in the last
`PRISM_APP_FEEDBACK_WINDOW_DAYS` (14), `GET /repos/{repo}/pulls/comments/{id}/reactions` with
`If-None-Match` set to the last ETag. GitHub answers 304 for an unchanged list and does not count
it against the rate limit; this was checked live (the remaining quota stayed at 4972 across a
304). The ETag is tied to the token, and installation tokens last an hour, so the saving applies
within the long-running beat worker; a separate process with a new token reads everything once.

**Replies and resolved threads** come by webhook: `pull_request_review_comment` (created, with
`in_reply_to_id` pointing at a PRism comment) adds a reply; `pull_request_review_thread`
(resolved / unresolved) sets the flag for the PRism comments in the thread. The App must be
subscribed to both events.

**Label and score.** good when 👍 outnumber 👎, bad when 👎 outnumber 👍, mixed on a tie with
votes; with no votes, a resolved thread is good (the author acted on it) and anything else has no
label. Only labelled feedback is sent. Langfuse gets a `comment_feedback` score on the review's
trace, from -1 (all 👎) to 1 (all 👍), and an item in the `prism-comment-feedback` dataset with the
hunk as input, the comment as expected output and the label and counts as metadata. Score and
item ids are `prism-finding-<id>`, so new reactions update them instead of adding duplicates.
Feedback is sent again only when it changes.

**Also in this stage:** a review with nothing to post is posted only when it is PRism's first
review on the PR (`PRISM_APP_POST_EMPTY=first`), so later pushes with nothing to add stay quiet.
And posted comments are matched to findings through the PR's comment list: GitHub's
`/reviews/{id}/comments` returns `line: null`, so before this fix no comment id was ever stored.

## Checked live

On the test repo's PR #2 (an order report with an unsafe `yaml.load`, a mutable default argument
and a division by an empty list's length), PRism's review was posted with three comments. One
was given 👍, one 👎 and its thread resolved, one a reply. The reply arrived by webhook; one
feedback pass stored all three, labelled the first good and the second bad (a 👎 outweighs the
resolve), and sent those two to Langfuse: two `comment_feedback` scores on the review's trace
(+1 and -1, read back through the metrics API, since Langfuse v4 has switched off its score read
endpoints) and two dataset items. A second pass in the same process got three 304s.

## Consequences

- A reply alone has no label. Replies are stored but not yet read for sentiment.
- The dataset holds comments, not pairs; pairing a bad comment with a better one for the same
  hunk is the next training round's job.
