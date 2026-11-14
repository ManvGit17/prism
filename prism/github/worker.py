"""The App's review worker: a PR head commit in, a GitHub review with inline comments out.

    handle(job): PR still open and still at this commit? -> diff -> shallow checkout ->
                 review (prism.review.service) -> held, or posted

A review the verifier has not answered yet is **held** (ADR 0017): nothing unverified is posted.
`job.json` in the review's work directory remembers the PR, `pending_reviews` (MCP) shows it to a
Claude session, and once that session has answered, `resume()` runs the review again (the
reviewer's replies are cached) and posts it.

Before posting, the worker checks the PR head again; if a newer commit was pushed meanwhile, the
review is dropped as superseded, since its line numbers may no longer match. A review that was
already posted is never posted twice (`github_review_id` on the stored review).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from prism.config import Settings
from prism.github.api import AppAuth, GitHubClient, GitHubError, installations
from prism.review.service import ReviewOutcome, ReviewRequest, review_async
from prism.store.reviews import ReviewStore, StoredReview

log = logging.getLogger(__name__)

Result = Literal["posted", "held", "superseded", "closed", "too_large", "already_posted", "error"]
JOB_FILE = "job.json"
TRIGGER_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


@dataclass(frozen=True)
class PullJob:
    installation_id: int
    repo: str  # owner/name
    number: int
    head_sha: str

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> PullJob | None:
        """A job for a `pull_request` webhook payload that should be reviewed, else None."""
        pr = event.get("pull_request") or {}
        if (
            event.get("action") not in TRIGGER_ACTIONS
            or pr.get("draft")
            or pr.get("state") != "open"
        ):
            return None
        return cls(
            installation_id=int(event["installation"]["id"]),
            repo=str(event["repository"]["full_name"]),
            number=int(pr["number"]),
            head_sha=str(pr["head"]["sha"]),
        )


Checkout = Callable[[PullJob, str, Path], Path]
ReviewFn = Callable[..., Awaitable[ReviewOutcome]]


def git_checkout(job: PullJob, token: str, root: Path, web_url: str = "https://github.com") -> Path:
    """A shallow checkout of the PR head commit under `root`. The token goes in a per-command
    header, never into .git/config or the remote URL."""
    dest = root / job.repo.replace("/", "__") / job.head_sha
    if (dest / ".git").is_dir():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    auth = ["-c", f"http.extraHeader=Authorization: Basic {basic}"]

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(dest), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )

    git("init", "-q")
    git(*auth, "fetch", "-q", "--depth", "1", f"{web_url}/{job.repo}.git", job.head_sha)
    git("checkout", "-q", "FETCH_HEAD")
    return dest


def web_url_for(api_url: str) -> str:
    api = api_url.rstrip("/")
    if api == "https://api.github.com":
        return "https://github.com"
    return api.removesuffix("/api/v3")


def comment_body(f: Any) -> str:
    body = f.comment
    if f.suggested_fix:
        body += f"\n\n**Suggested fix:** {f.suggested_fix}"
    who = "Claude" if f.author.startswith("claude") else f.author
    body += (
        f"\n\n<sub>PRism · {f.severity} {f.category} · from {who} · "
        f"confidence {f.confidence or 0:.2f} · finding {f.id}</sub>"
    )
    return str(body)


def review_body(stored: StoredReview) -> str:
    return f"**PRism review: risk {stored.verdict}**\n\n{stored.summary}"


class AppWorker:
    def __init__(
        self,
        auth: AppAuth,
        settings: Settings,
        store: ReviewStore,
        *,
        checkout: Checkout | None = None,
        review_fn: ReviewFn = review_async,
    ) -> None:
        self.auth = auth
        self.s = settings
        self.store = store
        self.web_url = web_url_for(settings.github_api_url)
        self.checkout = checkout or (
            lambda job, tok, root: git_checkout(job, tok, root, self.web_url)
        )
        self.review_fn = review_fn
        # One review at a time: they all share one local model (a queue comes in stage 4).
        self._lock = asyncio.Lock()

    async def handle(self, job: PullJob) -> Result:
        """Review one PR head; errors are logged and returned as "error" (in-process use)."""
        try:
            return await self.process(job)
        except (GitHubError, subprocess.SubprocessError, OSError) as exc:
            log.error("review of %s#%d failed: %s", job.repo, job.number, exc)
            return "error"

    async def process(self, job: PullJob) -> Result:
        """Review one PR head; errors raise, so a queue can retry them."""
        async with self._lock:
            return await self._handle(job)

    async def _handle(self, job: PullJob) -> Result:
        token = self.auth.installation_token(job.installation_id)
        client = self.auth.client(job.installation_id)
        try:
            pr = await asyncio.to_thread(client.pull, job.repo, job.number)
            if pr.get("state") != "open":
                return "closed"
            if pr["head"]["sha"] != job.head_sha:
                return "superseded"
            if (
                int(pr.get("additions", 0)) + int(pr.get("deletions", 0))
                > self.s.app_max_changed_lines
            ):
                log.info("%s#%d is too large to review", job.repo, job.number)
                return "too_large"
            diff = await asyncio.to_thread(client.pull_diff, job.repo, job.number)
            root = Path(self.s.app_checkout_dir)
            checkout = await asyncio.to_thread(self.checkout, job, token, root)
            outcome = await self.review_fn(
                ReviewRequest(
                    repo=checkout,
                    diff_text=diff,
                    repo_id=job.repo,
                    pr_number=job.number,
                    store="memory",
                    mode="graph",
                ),
                settings=self.s,
                notes=log.info,
                review_store=self.store,
            )
            (outcome.work / JOB_FILE).parent.mkdir(parents=True, exist_ok=True)
            (outcome.work / JOB_FILE).write_text(json.dumps(asdict(job)), encoding="utf-8")
            if outcome.status != "complete" or outcome.stored is None:
                log.info(
                    "%s#%d held: %d answer(s) needed in %s",
                    job.repo,
                    job.number,
                    outcome.pending,
                    outcome.verify_dir,
                )
                return "held"
            return await self._post(client, job, outcome.stored)
        finally:
            client.close()

    async def _post(self, client: GitHubClient, job: PullJob, stored: StoredReview) -> Result:
        if stored.github_review_id is not None:
            return "already_posted"
        pr = await asyncio.to_thread(client.pull, job.repo, job.number)
        if pr["head"]["sha"] != job.head_sha:
            return "superseded"
        posted = [f for f in stored.findings if f.posted]
        comments = [
            {"path": f.path, "line": f.line, "side": "RIGHT", "body": comment_body(f)}
            for f in posted
        ]
        review = await asyncio.to_thread(
            lambda: client.create_review(
                job.repo,
                job.number,
                commit_id=job.head_sha,
                body=review_body(stored),
                comments=comments,
            )
        )
        made = await asyncio.to_thread(client.review_comments, job.repo, job.number, review["id"])
        by_spot = {(c["path"], c.get("line")): int(c["id"]) for c in made}
        ids = {f.id: by_spot[(f.path, f.line)] for f in posted if (f.path, f.line) in by_spot}
        self.store.mark_posted(stored.id, int(review["id"]), ids)
        log.info(
            "posted review %s on %s#%d with %d comment(s)",
            review["id"],
            job.repo,
            job.number,
            len(comments),
        )
        return "posted"

    async def resume(self) -> dict[str, Result]:
        """Run every held review again; post the ones whose verification is now answered."""
        out: dict[str, Result] = {}
        for r in self.store.history(limit=200):
            if r.status != "awaiting_verification" or not r.work_dir:
                continue
            path = Path(r.work_dir) / JOB_FILE
            if not path.is_file():
                continue
            job = PullJob(**json.loads(path.read_text(encoding="utf-8")))
            out[f"{job.repo}#{job.number}@{job.head_sha[:7]}"] = await self.handle(job)
        return out

    async def find_unreviewed(self) -> list[PullJob]:
        """Open, non-draft PRs on every installed repo whose head commit has no review yet."""
        jobs: list[PullJob] = []
        for inst in await asyncio.to_thread(installations, self.auth):
            client = self.auth.client(inst)
            try:
                repos = await asyncio.to_thread(client.installation_repos)
                for repo in repos:
                    for pr in await asyncio.to_thread(client.open_pulls, repo):
                        if pr.get("draft"):
                            continue
                        sha = pr["head"]["sha"]
                        seen = self.store.history(repo, int(pr["number"]), limit=20)
                        if any(r.head_sha == sha for r in seen):
                            continue
                        jobs.append(PullJob(inst, repo, int(pr["number"]), sha))
            finally:
                client.close()
        return jobs

    async def poll_once(self) -> dict[str, Result]:
        """Review, in this process, every open PR head that has no review yet."""
        out: dict[str, Result] = {}
        for job in await self.find_unreviewed():
            out[f"{job.repo}#{job.number}@{job.head_sha[:7]}"] = await self.handle(job)
        return out
