# ADR 0017: The GitHub App holds reviews until Claude has verified them

Date: 2026-11-14. Status: accepted.

## Context

PLAN.md Phase 6 asks for a GitHub App that reviews pull requests and posts inline comments:
webhook HMAC check, installation-token auth, diff fetch, a review with inline comments, and
smee.io or polling for local development. The verifier is a Claude handoff (ADR 0011) and the
machine has no API key, so a review cannot always finish inside the webhook's lifetime, and the
local model's comments are not fit to post unverified (eval/reports/review_pipeline.md).

## Decision

**Auth** (`prism/github/api.py`). The App signs a 9-minute RS256 JWT with its private key and
exchanges it for an installation token, cached until 5 minutes before it expires. The key file
stays outside the repo (`PRISM_GITHUB_PRIVATE_KEY_PATH`); the App id and the webhook secret are in
the git-ignored `.env`. A 403 or 429 that carries `retry-after`, or `x-ratelimit-remaining: 0`,
waits and retries, capped at 60 s and 3 retries.

**Webhook** (`prism/github/webhook.py`, `prism app serve`). The `X-Hub-Signature-256` HMAC is
checked before the body is read as JSON; a bad signature is a 401. Repeated delivery ids are
dropped. `pull_request` events with `opened`, `synchronize`, `reopened` or `ready_for_review` on a
non-draft PR are accepted with 202 and reviewed in the background. For local development a
smee.io channel forwards deliveries to 127.0.0.1; `prism app poll` is the fallback with no
tunnel: it lists open PRs on the installed repos and reviews head commits it has not reviewed.

**Worker** (`prism/github/worker.py`). For each PR head commit: is the PR still open, still at that
commit, and under `PRISM_APP_MAX_CHANGED_LINES`? Then the diff, a shallow checkout of the head
commit (the token goes in a per-command `http.extraHeader`, never in `.git/config` or the remote
URL), and the shared review service (ADR 0016). If the verifier has not answered, the review is
**held**: stored as `awaiting_verification` with a `job.json` naming the PR, and nothing is
posted. A Claude session sees it through the MCP `pending_reviews` tool and answers it;
`prism app resume` (also run on every poll) reruns the review from cached replies and posts it.
Before posting, the head commit is checked again: if someone pushed meanwhile the result is
dropped as superseded, because its line numbers may be stale; the new push brings its own review.
A posted review records its GitHub review id and each comment id (migration 3), so it is never
posted twice and the feedback loop can find the comments.

**What is posted.** One review with event `COMMENT` (never `APPROVE` or `REQUEST_CHANGES`): the
summary as its body, one inline comment per kept finding on the new-file line, with the suggested
fix if there is one and a footer naming the severity, category, author and finding id.

**Checked live.** A private test repo, `prism-playground`, with the App installed on it alone.
PR #1 added a coupon lookup built with an f-string SQL query and a `remove(position)` that
documents 1-based positions but calls `pop(position)`. The `opened` webhook went GitHub to smee
to 127.0.0.1, passed the HMAC check and was queued. The worker read the PR, fetched the diff,
checked out the head commit and reviewed it: the one hunk was escalated to Claude as
security-sensitive and Bandit flagged the SQL, so the review was held with 2 answers needed.
A Claude session answered both; on the next poll the review was rerun from cached replies and
posted by the App's bot as one `COMMENT` review with 3 inline comments: the SQL injection
(critical, with a parameterized-query fix), the off-by-one (major, fix `pop(position - 1)`)
and the missing tests (minor). Bandit's own SQL comment was merged into Claude's on the same
line. Polling found the PR on its own after a server restart.

## Consequences

- A PR's first review can take as long as it takes someone to answer the handoff. With
  `PRISM_VERIFIER=api` it posts within the webhook's background task.
- Reviews run one at a time in the server process; stage 4 moves them to a Celery queue with
  retries and per-installation limits.
