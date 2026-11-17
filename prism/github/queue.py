"""The review queue: Celery tasks over Redis (ADR 0018).

    webhook / poll --enqueue()--> Redis --> worker: review_pull(job)
                                            beat:   resume_held every minute (posts answered
                                                    held reviews), poll_open_prs (optional)

What the queue adds over reviewing inside the web process:

* **Retries.** A GitHub rate limit or a 5xx raises from `AppWorker.process`; the task retries
  with exponential backoff (at most 5 times). A crashed worker does not lose the review:
  tasks are acknowledged late and rejected on worker loss, and the broker's visibility timeout
  is above the longest review, so a slow review is not handed to a second worker meanwhile.
* **Idempotency.** `repo#pr#head_sha` is claimed in Redis before a review starts; a webhook
  redelivery or a poll that finds the same commit is dropped. A failed review releases its
  claim so its retry can run.
* **Superseded pushes.** Every enqueue records the PR's newest head commit; a queued review of
  an older commit sees that and stops before calling any model. (`AppWorker` also checks the
  head again before posting.)
* **Per-installation limit.** A review takes one of `PRISM_APP_REVIEWS_PER_INSTALLATION` slots
  for its installation; with none free the task waits and retries, so one busy repo cannot
  take every worker or the installation's API quota.

`Coordinator` holds that state: `RedisCoordinator` in production, `MemoryCoordinator` in tests.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any, Protocol

from prism.config import Settings, get_settings
from prism.github.worker import AppWorker, PullJob, Result

log = logging.getLogger(__name__)

CLAIM_TTL_S = 24 * 3600
SLOT_TTL_S = 2 * 3600


def pr_key(job: PullJob) -> str:
    return f"{job.repo}#{job.number}"


def job_key(job: PullJob) -> str:
    return f"{job.repo}#{job.number}#{job.head_sha}"


class Coordinator(Protocol):
    def set_latest(self, pr: str, sha: str) -> None: ...

    def latest(self, pr: str) -> str | None: ...

    def claim(self, key: str, ttl_s: int = CLAIM_TTL_S) -> bool:
        """True for the first caller; False if `key` is already claimed."""
        ...

    def release(self, key: str) -> None: ...

    def take_slot(self, installation: int, limit: int) -> bool: ...

    def free_slot(self, installation: int) -> None: ...


class MemoryCoordinator:
    def __init__(self) -> None:
        self.heads: dict[str, str] = {}
        self.claims: set[str] = set()
        self.slots: dict[int, int] = {}

    def set_latest(self, pr: str, sha: str) -> None:
        self.heads[pr] = sha

    def latest(self, pr: str) -> str | None:
        return self.heads.get(pr)

    def claim(self, key: str, ttl_s: int = CLAIM_TTL_S) -> bool:
        if key in self.claims:
            return False
        self.claims.add(key)
        return True

    def release(self, key: str) -> None:
        self.claims.discard(key)

    def take_slot(self, installation: int, limit: int) -> bool:
        if self.slots.get(installation, 0) >= limit:
            return False
        self.slots[installation] = self.slots.get(installation, 0) + 1
        return True

    def free_slot(self, installation: int) -> None:
        self.slots[installation] = max(0, self.slots.get(installation, 0) - 1)


class RedisCoordinator:
    def __init__(self, client: Any, prefix: str = "prism") -> None:
        self.r = client
        self.p = prefix

    @classmethod
    def from_url(cls, url: str) -> RedisCoordinator:
        import redis

        return cls(redis.Redis.from_url(url, decode_responses=True))

    def set_latest(self, pr: str, sha: str) -> None:
        self.r.set(f"{self.p}:head:{pr}", sha, ex=7 * 24 * 3600)

    def latest(self, pr: str) -> str | None:
        value = self.r.get(f"{self.p}:head:{pr}")
        return None if value is None else str(value)

    def claim(self, key: str, ttl_s: int = CLAIM_TTL_S) -> bool:
        return bool(self.r.set(f"{self.p}:claim:{key}", "1", nx=True, ex=ttl_s))

    def release(self, key: str) -> None:
        self.r.delete(f"{self.p}:claim:{key}")

    def take_slot(self, installation: int, limit: int) -> bool:
        key = f"{self.p}:slots:{installation}"
        used = int(self.r.incr(key))
        # A crashed worker never frees its slot; the expiry does, after the longest review.
        self.r.expire(key, SLOT_TTL_S)
        if used > limit:
            self.r.decr(key)
            return False
        return True

    def free_slot(self, installation: int) -> None:
        key = f"{self.p}:slots:{installation}"
        if int(self.r.decr(key)) < 0:
            self.r.set(key, 0, ex=SLOT_TTL_S)


class SlotBusy(Exception):
    """No review slot free for this installation; the task retries later."""


def enqueue_job(coord: Coordinator, job: PullJob, send: Any) -> None:
    """Record the newest head of the PR, then queue the review."""
    coord.set_latest(pr_key(job), job.head_sha)
    send(asdict(job))


def run_review(
    worker: AppWorker, coord: Coordinator, job: PullJob, per_installation: int
) -> Result | str:
    """What the review task does, without Celery: supersede check, claim, slot, review."""
    newest = coord.latest(pr_key(job))
    if newest is not None and newest != job.head_sha:
        log.info("%s at %s superseded by %s", pr_key(job), job.head_sha[:7], newest[:7])
        return "superseded"
    key = job_key(job)
    if not coord.claim(key):
        return "duplicate"
    if not coord.take_slot(job.installation_id, per_installation):
        coord.release(key)
        raise SlotBusy(f"installation {job.installation_id} is at its limit")
    try:
        return asyncio.run(worker.process(job))
    except Exception:
        coord.release(key)  # let the retry run
        raise
    finally:
        coord.free_slot(job.installation_id)


def make_celery(settings: Settings | None = None) -> Any:
    from celery import Celery

    s = settings or get_settings()
    app = Celery("prism", broker=s.redis_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_default_queue="prism-reviews",
        timezone="UTC",
        # Above the longest review, so a slow review is not redelivered to another worker.
        broker_transport_options={"visibility_timeout": int(max(3600, 3 * s.review_time_limit_s))},
        beat_schedule={
            "resume-held": {"task": "prism.resume_held", "schedule": 60.0},
            "collect-feedback": {
                "task": "prism.collect_feedback",
                "schedule": s.app_feedback_interval_s,
            },
            **(
                {"poll": {"task": "prism.poll_open_prs", "schedule": s.app_poll_interval_s}}
                if s.app_poll_with_beat
                else {}
            ),
        },
    )
    return app


celery_app = make_celery()

_state: dict[str, Any] = {}


def _deps() -> tuple[AppWorker, Coordinator, Settings]:
    """One worker and coordinator per worker process, built on first use."""
    if "worker" not in _state:
        from prism.github.api import AppAuth
        from prism.mcp_server import PrismTools

        s = get_settings()
        _state["settings"] = s
        _state["worker"] = AppWorker(AppAuth.from_settings(s), s, PrismTools(s).store)
        _state["coord"] = RedisCoordinator.from_url(s.redis_url)
    return _state["worker"], _state["coord"], _state["settings"]


@celery_app.task(  # type: ignore[untyped-decorator]
    name="prism.review_pull",
    bind=True,
    max_retries=5,
    acks_late=True,
)
def review_pull(self: Any, job: dict[str, Any]) -> str:
    from prism.github.api import GitHubError

    worker, coord, s = _deps()
    pull = PullJob(**job)
    try:
        result = run_review(worker, coord, pull, s.app_reviews_per_installation)
    except SlotBusy as exc:
        raise self.retry(exc=exc, countdown=30) from exc
    except GitHubError as exc:
        if exc.status in (403, 429) or (exc.status or 0) >= 500 or exc.status is None:
            raise self.retry(exc=exc, countdown=min(600, 30 * 2**self.request.retries)) from exc
        raise
    log.info("review_pull %s: %s", job_key(pull), result)
    return str(result)


@celery_app.task(name="prism.resume_held")  # type: ignore[untyped-decorator]
def resume_held() -> dict[str, str]:
    worker, _, _ = _deps()
    return {k: str(v) for k, v in asyncio.run(worker.resume()).items()}


@celery_app.task(name="prism.poll_open_prs")  # type: ignore[untyped-decorator]
def poll_open_prs() -> int:
    """Queue a review for each open PR head the App has not reviewed."""
    worker, coord, _ = _deps()
    jobs = asyncio.run(worker.find_unreviewed())
    for job in jobs:
        enqueue_job(coord, job, review_pull.delay)
    return len(jobs)


def feedback_collector(worker: AppWorker, s: Settings) -> Any:
    """Reactions go to Langfuse when tracing or prompts use it, else only to the store."""
    from prism.github.feedback import FeedbackCollector, LangfuseFeedbackSink

    sink = None
    if s.tracing == "langfuse" or s.prompt_source == "langfuse":
        from prism.observability.tracing import LangfuseTracer

        sink = LangfuseFeedbackSink(LangfuseTracer.from_settings(s).client, s.feedback_dataset)
    return FeedbackCollector(
        worker.auth, worker.store, sink, window_days=s.app_feedback_window_days
    )


@celery_app.task(name="prism.collect_feedback")  # type: ignore[untyped-decorator]
def collect_feedback() -> dict[str, int]:
    worker, _, s = _deps()
    counts: dict[str, int] = feedback_collector(worker, s).collect()
    log.info("feedback: %s", counts)
    return counts
