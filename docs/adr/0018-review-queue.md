# ADR 0018: A Celery queue for App reviews

Date: 2026-11-14. Status: accepted.

## Context

In stage 3 the web process reviewed PRs itself, one at a time, after answering the webhook. That
loses a review if the process dies, cannot retry a GitHub rate limit, reviews an old commit after
a newer push, and lets one busy repo hold the only reviewer. PLAN.md Phase 6 asks for Celery
tasks with retries, an idempotency key `repo#pr#head_sha`, rate-limit handling, a broker
visibility timeout above the longest review with late acknowledgement, cancelling superseded
pushes, and a concurrency limit per installation. Phase 0 said Celery workers run in a container
only, because Celery's default pool does not run on Windows.

## Decision

**Tasks** (`prism/github/queue.py`, Redis as broker). With `PRISM_APP_QUEUE=celery` the webhook
queues `prism.review_pull(job)` instead of reviewing. `prism.resume_held` runs every minute from
beat and posts held reviews whose verification has been answered; `prism.poll_open_prs` queues
reviews for open PR heads nobody has reviewed, when `PRISM_APP_POLL_WITH_BEAT` is on.

**Reliability.** Tasks are acknowledged late and rejected on worker loss, prefetch is 1, and the
broker's visibility timeout is three times the review time limit (at least an hour), so a slow
review is neither lost nor handed to a second worker.

**Idempotency and superseded pushes.** Every enqueue records the PR's newest head commit in Redis.
A task first compares its commit with it and stops as `superseded` when a newer push exists, before
any model call; the worker also checks the head on GitHub before posting. It then claims
`repo#pr#head_sha` (`SET NX`, a day); a webhook redelivery or a poll of the same commit finds the
claim and stops as `duplicate`. A failed task releases its claim so its retry can run.

**Per-installation limit.** A task takes one of `PRISM_APP_REVIEWS_PER_INSTALLATION` slots (a
Redis counter with an expiry, so a crashed worker cannot hold one forever); with none free it
retries in 30 s.

**Retries.** `AppWorker.process` raises instead of swallowing errors. A GitHub 403 or 429 (rate
limits, including secondary ones), a 5xx or a network failure retries with backoff (30 s,
doubling, at most 600 s, 5 times); the client has already waited out a `retry-after` up to 60 s.
A 404 or other client error fails at once.

**Where the worker runs.** On Windows, `prism app worker` uses Celery's solo pool, which does run
there, and reaches the host's Ollama, the SAST tools and PMD directly. A `Dockerfile` and a
`worker` Compose profile (worker and beat) are there for Linux hosts; they are not built on the
build machine, whose C: drive (where Docker keeps images) is short of space, and the image leaves
PMD out.

## Checked live

With Redis from Compose, the worker, beat, the webhook server in queue mode and the smee tunnel
running, a push to the test PR (applying the review's own suggested fixes) arrived as a
`synchronize` webhook and was queued; the worker reviewed it in 18 s and held it with one answer
needed. Once a Claude session answered, beat's next `resume_held` posted the review.

## Consequences

- With the solo pool one worker process reviews one PR at a time; more throughput means more
  worker processes, each with its own Ollama calls under the same server limit.
- Coordination state lives in Redis; `MemoryCoordinator` mirrors it for tests.
