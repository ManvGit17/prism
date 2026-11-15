"""The webhook endpoint (ADR 0017).

`POST /webhook` checks the `X-Hub-Signature-256` HMAC against the App's webhook secret before it
reads anything else, answers at once, and runs the review in the background. Only `pull_request`
events that open, reopen, push to or un-draft a PR start a review. A delivery id already seen is
ignored, since GitHub redelivers on timeouts.

No `from __future__ import annotations` here: FastAPI reads the endpoint's annotations at
runtime, and `Request` is imported inside `create_app`.
"""

import hashlib
import hmac
import json
import logging
from collections import deque
from collections.abc import Callable
from typing import Any

from prism.github.feedback import on_review_comment, on_review_thread
from prism.github.worker import AppWorker, PullJob

log = logging.getLogger(__name__)


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def create_app(
    worker: AppWorker, secret: str, enqueue: Callable[[PullJob], None] | None = None
) -> Any:
    """The web app. With `enqueue` (the Celery queue) a review is queued; without, it runs in
    this process after the response."""
    from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

    app = FastAPI(title="PRism", docs_url=None, redoc_url=None)
    seen: deque[str] = deque(maxlen=1000)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook", status_code=202)
    async def webhook(
        request: Request,
        background: BackgroundTasks,
        x_hub_signature_256: str | None = Header(default=None),
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
    ) -> dict[str, Any]:
        body = await request.body()
        if not valid_signature(secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="bad signature")
        if x_github_delivery and x_github_delivery in seen:
            return {"status": "duplicate"}
        if x_github_delivery:
            seen.append(x_github_delivery)
        if x_github_event == "ping":
            return {"status": "pong"}
        event = json.loads(body)
        # Feedback (ADR 0019): replies to PRism's comments and resolved threads.
        if x_github_event == "pull_request_review_comment":
            counted = on_review_comment(worker.store, event)
            return {"status": "feedback" if counted else "ignored", "event": x_github_event}
        if x_github_event == "pull_request_review_thread":
            counted = on_review_thread(worker.store, event)
            return {"status": "feedback" if counted else "ignored", "event": x_github_event}
        if x_github_event != "pull_request":
            return {"status": "ignored", "event": x_github_event}
        job = PullJob.from_event(event)
        if job is None:
            return {"status": "ignored", "event": x_github_event}
        if enqueue is not None:
            enqueue(job)
        else:
            background.add_task(worker.handle, job)
        log.info("queued review of %s#%d at %s", job.repo, job.number, job.head_sha[:7])
        return {"status": "queued", "repo": job.repo, "pr": job.number, "sha": job.head_sha}

    return app
