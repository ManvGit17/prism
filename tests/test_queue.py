"""The review queue: idempotency, superseded pushes, per-installation slots, retries."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("celery")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from prism.config import Settings
from prism.github import queue
from prism.github.api import GitHubError
from prism.github.queue import (
    MemoryCoordinator,
    RedisCoordinator,
    SlotBusy,
    enqueue_job,
    job_key,
    make_celery,
    pr_key,
    run_review,
)
from prism.github.webhook import create_app
from prism.github.worker import PullJob

JOB = PullJob(5, "acme/shop", 7, "a" * 40)
NEWER = PullJob(5, "acme/shop", 7, "b" * 40)


class FakeWorker:
    def __init__(self, result: str = "posted", error: Exception | None = None) -> None:
        self.result, self.error, self.jobs = result, error, []  # type: ignore[var-annotated]

    async def process(self, job: PullJob) -> str:
        self.jobs.append(job)
        if self.error is not None:
            raise self.error
        return self.result


class FakeRedis:
    """The handful of Redis commands RedisCoordinator uses."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def incr(self, key: str) -> int:
        self.data[key] = int(self.data.get(key, 0)) + 1
        return int(self.data[key])

    def decr(self, key: str) -> int:
        self.data[key] = int(self.data.get(key, 0)) - 1
        return int(self.data[key])

    def expire(self, key: str, ttl: int) -> None:
        return None


@pytest.fixture(params=["memory", "redis"])
def coord(request: pytest.FixtureRequest) -> Any:
    return MemoryCoordinator() if request.param == "memory" else RedisCoordinator(FakeRedis())


def test_coordinator_rules(coord: Any) -> None:
    assert coord.latest("p") is None
    coord.set_latest("p", "s1")
    coord.set_latest("p", "s2")
    assert coord.latest("p") == "s2"
    assert coord.claim("k") and not coord.claim("k")
    coord.release("k")
    assert coord.claim("k")
    assert coord.take_slot(5, 2) and coord.take_slot(5, 2) and not coord.take_slot(5, 2)
    assert coord.take_slot(6, 2), "slots are per installation"
    coord.free_slot(5)
    assert coord.take_slot(5, 2)


def test_review_runs_once_per_commit(coord: Any) -> None:
    worker = FakeWorker()
    enqueue_job(coord, JOB, lambda payload: None)
    assert run_review(worker, coord, JOB, 1) == "posted"  # type: ignore[arg-type]
    assert run_review(worker, coord, JOB, 1) == "duplicate"  # type: ignore[arg-type]
    assert len(worker.jobs) == 1
    assert coord.take_slot(5, 1), "the slot was freed"


def test_a_newer_push_supersedes_a_queued_review(coord: Any) -> None:
    worker = FakeWorker()
    sent: list[dict[str, Any]] = []
    enqueue_job(coord, JOB, sent.append)
    enqueue_job(coord, NEWER, sent.append)
    assert [s["head_sha"] for s in sent] == [JOB.head_sha, NEWER.head_sha]
    assert coord.latest(pr_key(JOB)) == NEWER.head_sha
    assert run_review(worker, coord, JOB, 1) == "superseded"  # type: ignore[arg-type]
    assert run_review(worker, coord, NEWER, 1) == "posted"  # type: ignore[arg-type]
    assert worker.jobs == [NEWER]


def test_busy_installation_waits_and_failures_release(coord: Any) -> None:
    assert coord.take_slot(5, 1)
    with pytest.raises(SlotBusy):
        run_review(FakeWorker(), coord, JOB, 1)  # type: ignore[arg-type]
    assert coord.claim(job_key(JOB)), "a busy slot gives the claim back"
    coord.release(job_key(JOB))
    coord.free_slot(5)

    failing = FakeWorker(error=GitHubError("rate limited", 429))
    with pytest.raises(GitHubError):
        run_review(failing, coord, JOB, 1)  # type: ignore[arg-type]
    assert coord.claim(job_key(JOB)) and coord.take_slot(5, 1), "claim and slot are released"


# The Celery task


class RetryCalled(Exception):
    def __init__(self, countdown: float) -> None:
        super().__init__(countdown)
        self.countdown = countdown


@pytest.fixture
def task(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """review_pull run directly, with its dependencies and `retry` replaced."""
    state: dict[str, Any] = {"worker": FakeWorker(), "coord": MemoryCoordinator()}
    s = Settings(_env_file=None, app_reviews_per_installation=1)
    monkeypatch.setattr(queue, "_deps", lambda: (state["worker"], state["coord"], s))

    def fake_retry(exc: Exception | None = None, countdown: float = 0, **_: Any) -> Exception:
        return RetryCalled(countdown)

    monkeypatch.setattr(queue.review_pull, "retry", fake_retry)
    yield state


def test_task_reviews_once(task: dict[str, Any]) -> None:
    job = json.loads(json.dumps(JOB.__dict__))
    assert queue.review_pull.run(job) == "posted"
    assert queue.review_pull.run(job) == "duplicate"


@pytest.mark.parametrize("status", [403, 429, 502, None])
def test_task_retries_rate_limits_and_server_errors(task: dict[str, Any], status: Any) -> None:
    task["worker"] = FakeWorker(error=GitHubError("try later", status))
    with pytest.raises(RetryCalled) as err:
        queue.review_pull.run(json.loads(json.dumps(JOB.__dict__)))
    assert err.value.countdown == 30  # first retry; doubles each time, at most 600 s


def test_task_retries_when_the_installation_is_busy(task: dict[str, Any]) -> None:
    task["coord"].take_slot(5, 1)
    with pytest.raises(RetryCalled):
        queue.review_pull.run(json.loads(json.dumps(JOB.__dict__)))


def test_task_does_not_retry_a_client_error(task: dict[str, Any]) -> None:
    task["worker"] = FakeWorker(error=GitHubError("not found", 404))
    with pytest.raises(GitHubError):
        queue.review_pull.run(json.loads(json.dumps(JOB.__dict__)))


def test_celery_settings_protect_slow_reviews() -> None:
    app = make_celery(Settings(_env_file=None, review_time_limit_s=1800, app_poll_with_beat=True))
    conf = app.conf
    assert conf.task_acks_late and conf.task_reject_on_worker_lost
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_transport_options["visibility_timeout"] >= 3 * 1800
    assert set(conf.beat_schedule) == {"resume-held", "poll"}
    assert set(make_celery(Settings(_env_file=None)).conf.beat_schedule) == {"resume-held"}


def test_webhook_enqueues_when_a_queue_is_given() -> None:
    import hashlib
    import hmac

    queued: list[PullJob] = []

    class NoInline:
        async def handle(self, job: PullJob) -> str:
            raise AssertionError("must not review in the web process")

    client = TestClient(create_app(NoInline(), "s", queued.append))  # type: ignore[arg-type]
    body = json.dumps(
        {
            "action": "synchronize",
            "installation": {"id": 5},
            "repository": {"full_name": "acme/shop"},
            "pull_request": {
                "number": 7,
                "draft": False,
                "state": "open",
                "head": {"sha": "a" * 40},
            },
        }
    ).encode()
    sig = "sha256=" + hmac.new(b"s", body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "q1",
        },
    )
    assert resp.json()["status"] == "queued" and queued == [JOB]
