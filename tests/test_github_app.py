"""The GitHub App against a fake GitHub: auth, rate limits, webhook, worker, polling."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("jwt")
pytest.importorskip("fastapi")

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from prism.config import Settings
from prism.github.api import AppAuth, GitHubClient, app_jwt
from prism.github.webhook import create_app, valid_signature
from prism.github.worker import JOB_FILE, AppWorker, PullJob
from prism.review.service import ReviewOutcome
from prism.schemas import PRReview, Verdict
from prism.store.reviews import InMemoryReviewStore, StoredFinding, StoredReview

REPO, PR, SHA = "acme/shop", 7, "a" * 40
DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n x = 1\n+y = x / 0\n"


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem(key: rsa.RSAPrivateKey) -> str:
    return str(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
    )


class FakeGitHub:
    def __init__(self) -> None:
        self.head = SHA
        self.state = "open"
        self.additions = 1
        self.reviews: list[dict[str, Any]] = []
        self.tokens = 0
        self.calls: list[str] = []
        self.open_pulls = [{"number": PR, "draft": False, "head": {"sha": SHA}}]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append(f"{method} {path}")
        if method == "POST" and re.fullmatch(r"/app/installations/\d+/access_tokens", path):
            assert request.headers["authorization"].startswith("Bearer ")
            self.tokens += 1
            return httpx.Response(
                201, json={"token": f"ghs_{self.tokens}", "expires_at": "2099-01-01T00:00:00Z"}
            )
        if path == "/app/installations":
            return httpx.Response(200, json=[{"id": 5}])
        if path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": [{"full_name": REPO}]})
        if path == f"/repos/{REPO}/pulls" and method == "GET":
            return httpx.Response(200, json=self.open_pulls)
        if path == f"/repos/{REPO}/pulls/{PR}" and method == "GET":
            if request.headers["accept"] == "application/vnd.github.diff":
                return httpx.Response(200, text=DIFF)
            return httpx.Response(
                200,
                json={
                    "number": PR,
                    "state": self.state,
                    "head": {"sha": self.head},
                    "additions": self.additions,
                    "deletions": 0,
                },
            )
        if path == f"/repos/{REPO}/pulls/{PR}/reviews" and method == "POST":
            body = json.loads(request.content)
            self.reviews.append(body)
            return httpx.Response(200, json={"id": 900 + len(self.reviews)})
        m = re.fullmatch(rf"/repos/{REPO}/pulls/{PR}/reviews/(\d+)/comments", path)
        if m:
            body = self.reviews[int(m.group(1)) - 901]
            return httpx.Response(
                200,
                json=[
                    {"id": 5000 + i, "path": c["path"], "line": c["line"], "body": c["body"]}
                    for i, c in enumerate(body["comments"])
                ],
            )
        return httpx.Response(404, json={"message": f"no route {method} {path}"})


def finding(posted: bool = True) -> StoredFinding:
    return StoredFinding(
        path="x.py",
        line=2,
        hunk_header="@@ -1,1 +1,2 @@",
        hunk=DIFF,
        author="claude",
        comment="Division by zero: `x / 0` always raises.",
        posted=posted,
        decision="kept",
        severity="critical",
        category="bug",
        confidence=0.95,
        suggested_fix="remove the division",
    )


class FakeReview:
    """Stands in for review_async: held the first `held` times, then complete."""

    def __init__(self, store: InMemoryReviewStore, tmp: Path, held: int = 0) -> None:
        self.store, self.tmp, self.held, self.calls = store, tmp, held, 0
        self.requests: list[Any] = []

    async def __call__(self, req: Any, **_: Any) -> ReviewOutcome:
        self.calls += 1
        self.requests.append(req)
        status = "awaiting_verification" if self.calls <= self.held else "complete"
        saved = self.store.save(
            StoredReview(
                key=f"{REPO}#{PR}#{SHA}#d",
                repo=REPO,
                pr_number=PR,
                head_sha=SHA,
                status=status,
                verdict="high" if status == "complete" else None,
                summary="Risk: high.",
                work_dir=str(self.tmp / "work"),
                findings=(finding(),) if status == "complete" else (),
            )
        )
        review = PRReview(repo=REPO, pr_number=PR, head_sha=SHA, summary="s", verdict=Verdict.HIGH)
        return ReviewOutcome(
            review=review,
            state={},
            status=status,  # type: ignore[arg-type]
            work=self.tmp / "work",
            pending=1 if status != "complete" else 0,
            stored=saved,
        )


def make_worker(
    key: rsa.RSAPrivateKey, tmp_path: Path, held: int = 0
) -> tuple[AppWorker, FakeGitHub, FakeReview, InMemoryReviewStore]:
    gh = FakeGitHub()
    store = InMemoryReviewStore()
    review = FakeReview(store, tmp_path, held)
    auth = AppAuth("123", pem(key), transport=httpx.MockTransport(gh))
    s = Settings(_env_file=None, app_checkout_dir=str(tmp_path / "co"))
    checkouts: list[str] = []

    def checkout(job: PullJob, token: str, root: Path) -> Path:
        checkouts.append(token)
        return tmp_path

    worker = AppWorker(auth, s, store, checkout=checkout, review_fn=review)
    return worker, gh, review, store


JOB = PullJob(installation_id=5, repo=REPO, number=PR, head_sha=SHA)


# Auth and the client


def test_app_jwt(key: rsa.RSAPrivateKey) -> None:
    token = app_jwt("123", pem(key), now=1_000_000)
    claims = jwt.decode(
        token,
        key.public_key(),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims == {"iss": "123", "iat": 999_940, "exp": 1_000_540}


def test_installation_token_is_cached(key: rsa.RSAPrivateKey) -> None:
    gh = FakeGitHub()
    auth = AppAuth("123", pem(key), transport=httpx.MockTransport(gh))
    assert auth.installation_token(5) == auth.installation_token(5) == "ghs_1"
    assert gh.tokens == 1


def test_rate_limit_waits_and_retries() -> None:
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if hits["n"] == 1:
            return httpx.Response(403, headers={"retry-after": "7"}, json={"message": "slow"})
        return httpx.Response(200, json={"ok": True})

    slept: list[float] = []
    client = GitHubClient("t", transport=httpx.MockTransport(handler), sleep=slept.append)
    assert client.request("GET", "/x").json() == {"ok": True}
    assert slept == [7.0]


# Webhook


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_check() -> None:
    assert valid_signature("s", b"{}", sign("s", b"{}"))
    assert not valid_signature("s", b"{}", sign("other", b"{}"))
    assert not valid_signature("", b"{}", sign("", b"{}"))
    assert not valid_signature("s", b"{}", None)


class RecordingWorker:
    def __init__(self) -> None:
        self.jobs: list[PullJob] = []

    async def handle(self, job: PullJob) -> str:
        self.jobs.append(job)
        return "posted"


def event(action: str = "opened", draft: bool = False) -> dict[str, Any]:
    return {
        "action": action,
        "installation": {"id": 5},
        "repository": {"full_name": REPO},
        "pull_request": {"number": PR, "draft": draft, "state": "open", "head": {"sha": SHA}},
    }


def post(
    client: TestClient,
    payload: dict[str, Any],
    *,
    kind: str = "pull_request",
    delivery: str = "d1",
    secret: str = "s3cret",
) -> Any:
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": sign(secret, body),
            "X-GitHub-Event": kind,
            "X-GitHub-Delivery": delivery,
            "Content-Type": "application/json",
        },
    )


def test_webhook_queues_reviews_and_rejects_bad_signatures() -> None:
    worker = RecordingWorker()
    client = TestClient(create_app(worker, "s3cret"))  # type: ignore[arg-type]
    assert post(client, event(), secret="wrong").status_code == 401
    assert post(client, {"zen": "hi"}, kind="ping", delivery="p").json() == {"status": "pong"}
    resp = post(client, event())
    assert resp.status_code == 202 and resp.json()["status"] == "queued"
    assert worker.jobs == [JOB]
    assert post(client, event()).json() == {"status": "duplicate"}
    assert post(client, event(draft=True), delivery="d2").json()["status"] == "ignored"
    assert post(client, event(action="closed"), delivery="d3").json()["status"] == "ignored"
    assert post(client, {}, kind="issues", delivery="d4").json()["status"] == "ignored"
    assert len(worker.jobs) == 1


# Worker


def test_complete_review_is_posted_inline(key: rsa.RSAPrivateKey, tmp_path: Path) -> None:
    worker, gh, review, store = make_worker(key, tmp_path)
    assert asyncio.run(worker.handle(JOB)) == "posted"
    (posted,) = gh.reviews
    assert posted["event"] == "COMMENT" and posted["commit_id"] == SHA
    assert posted["body"].startswith("**PRism review: risk high**")
    (c,) = posted["comments"]
    assert (c["path"], c["line"], c["side"]) == ("x.py", 2, "RIGHT")
    assert "Division by zero" in c["body"] and "**Suggested fix:**" in c["body"]
    stored = store.history(REPO)[0]
    assert stored.github_review_id == 901 and stored.findings[0].github_comment_id == 5000
    assert review.requests[0].pr_number == PR and review.requests[0].diff_text == DIFF
    assert json.loads((tmp_path / "work" / JOB_FILE).read_text("utf-8"))["head_sha"] == SHA
    # Never twice.
    assert asyncio.run(worker.handle(JOB)) == "already_posted" and len(gh.reviews) == 1


def test_held_until_verified_then_resumed(key: rsa.RSAPrivateKey, tmp_path: Path) -> None:
    worker, gh, _, store = make_worker(key, tmp_path, held=1)
    assert asyncio.run(worker.handle(JOB)) == "held"
    assert gh.reviews == [] and store.history(REPO)[0].status == "awaiting_verification"
    assert asyncio.run(worker.resume()) == {f"{REPO}#{PR}@{SHA[:7]}": "posted"}
    assert len(gh.reviews) == 1 and asyncio.run(worker.resume()) == {}


def test_superseded_closed_and_too_large(key: rsa.RSAPrivateKey, tmp_path: Path) -> None:
    worker, gh, review, _ = make_worker(key, tmp_path)
    gh.head = "b" * 40
    assert asyncio.run(worker.handle(JOB)) == "superseded"
    gh.head, gh.state = SHA, "closed"
    assert asyncio.run(worker.handle(JOB)) == "closed"
    gh.state, gh.additions = "open", 10**6
    assert asyncio.run(worker.handle(JOB)) == "too_large"
    assert review.calls == 0 and gh.reviews == []


def test_push_during_review_drops_the_result(key: rsa.RSAPrivateKey, tmp_path: Path) -> None:
    worker, gh, review, _ = make_worker(key, tmp_path)
    original = review.__call__

    async def push_meanwhile(req: Any, **kw: Any) -> ReviewOutcome:
        out = await original(req, **kw)
        gh.head = "c" * 40
        return out

    worker.review_fn = push_meanwhile
    assert asyncio.run(worker.handle(JOB)) == "superseded" and gh.reviews == []


def test_poll_reviews_new_heads_once(key: rsa.RSAPrivateKey, tmp_path: Path) -> None:
    worker, gh, review, _ = make_worker(key, tmp_path)
    assert asyncio.run(worker.poll_once()) == {f"{REPO}#{PR}@{SHA[:7]}": "posted"}
    assert asyncio.run(worker.poll_once()) == {}
    gh.open_pulls = [{"number": PR + 1, "draft": True, "head": {"sha": "d" * 40}}]
    assert asyncio.run(worker.poll_once()) == {} and review.calls == 1


def test_from_event_needs_an_open_ready_pr() -> None:
    assert PullJob.from_event(event()) == JOB
    assert PullJob.from_event(event(action="synchronize")) == JOB
    assert PullJob.from_event(event(action="labeled")) is None
    assert PullJob.from_event(event(draft=True)) is None
    assert time.time() > 0


def test_git_checkout_fetches_one_commit_and_keeps_no_token(tmp_path: Path) -> None:
    import subprocess

    from prism.github.worker import git_checkout

    remote = tmp_path / "remote" / "acme" / "shop.git"
    src = tmp_path / "src"
    src.mkdir()

    def run(*a: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(a, check=True, capture_output=True, text=True)

    run("git", "-C", str(src), "init", "-q")
    (src / "x.py").write_text("x = 1\n", encoding="utf-8")
    run("git", "-C", str(src), "add", "-A")
    run(
        "git",
        "-C",
        str(src),
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "user.name=t",
        "commit",
        "-qm",
        "one",
    )
    sha = run("git", "-C", str(src), "rev-parse", "HEAD").stdout.strip()
    run("git", "clone", "-q", "--bare", str(src), str(remote))

    job = PullJob(5, "acme/shop", 1, sha)
    web = (tmp_path / "remote").as_uri()
    out = git_checkout(job, "ghs_secret_token", tmp_path / "co", web)
    assert (out / "x.py").read_text(encoding="utf-8") == "x = 1\n"
    assert run("git", "-C", str(out), "rev-parse", "HEAD").stdout.strip() == sha
    assert "ghs_secret_token" not in (out / ".git" / "config").read_text(encoding="utf-8")
    assert git_checkout(job, "ghs_secret_token", tmp_path / "co", web) == out  # reused


def test_empty_review_is_posted_only_first(key: rsa.RSAPrivateKey, tmp_path: Path) -> None:
    worker, gh, _, store = make_worker(key, tmp_path)
    store.save(
        StoredReview(
            key="earlier",
            repo=REPO,
            pr_number=PR,
            head_sha="e" * 40,
            status="complete",
            verdict="high",
        )
    )
    earlier = store.by_key("earlier")
    assert earlier is not None
    store.mark_posted(earlier.id, 800, {})

    async def nothing(req: Any, **kw: Any) -> ReviewOutcome:
        out = await FakeReview(store, tmp_path)(req, **kw)
        assert out.stored is not None
        saved = store.save(out.stored.model_copy(update={"findings": ()}))
        out.stored = saved
        return out

    worker.review_fn = nothing
    assert asyncio.run(worker.handle(JOB)) == "nothing_to_say" and gh.reviews == []

    worker2, gh2, _, store2 = make_worker(key, tmp_path / "fresh")

    async def nothing2(req: Any, **kw: Any) -> ReviewOutcome:
        out = await FakeReview(store2, tmp_path)(req, **kw)
        assert out.stored is not None
        out.stored = store2.save(out.stored.model_copy(update={"findings": ()}))
        return out

    worker2.review_fn = nothing2
    assert asyncio.run(worker2.handle(JOB)) == "posted", "the first review is posted even if empty"
    assert gh2.reviews[0]["comments"] == []
