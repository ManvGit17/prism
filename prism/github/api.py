"""GitHub App authentication and the few REST calls PRism makes (ADR 0017).

An App authenticates as itself with a short-lived JWT signed by its private key (RS256, at most 10
minutes), and exchanges it for an installation access token (1 hour) to act on a repo it is
installed on. Tokens are cached per installation until a few minutes before they expire.

Rate limits: a 403 or 429 with `retry-after`, or with `x-ratelimit-remaining: 0`, is a rate limit;
the call waits (`retry-after`, else until `x-ratelimit-reset`, at most `max_wait_s`) and tries
again, up to `retries` times. Other errors raise `GitHubError`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
USER_AGENT = "prism-review-bot"


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def app_jwt(app_id: str, private_key_pem: str, now: float | None = None) -> str:
    """A JWT for the App: issued 60 s in the past (clock drift), valid 9 minutes."""
    import jwt

    t = int(now if now is not None else time.time())
    payload = {"iat": t - 60, "exp": t + 9 * 60, "iss": app_id}
    token: str = jwt.encode(payload, private_key_pem, algorithm="RS256")
    return token


@dataclass
class AppAuth:
    app_id: str
    private_key_pem: str
    api_url: str = "https://api.github.com"
    transport: httpx.BaseTransport | None = None
    clock: Callable[[], float] = time.time
    _tokens: dict[int, tuple[str, float]] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, s: Any) -> AppAuth:
        if not s.github_app_id or not s.github_private_key_path:
            raise GitHubError("set PRISM_GITHUB_APP_ID and PRISM_GITHUB_PRIVATE_KEY_PATH in .env")
        pem = Path(s.github_private_key_path).expanduser().read_text(encoding="utf-8")
        return cls(s.github_app_id, pem, s.github_api_url)

    def app_client(self) -> GitHubClient:
        return GitHubClient(
            app_jwt(self.app_id, self.private_key_pem, self.clock()),
            self.api_url,
            transport=self.transport,
            bearer=True,
        )

    def installation_token(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached[1] - self.clock() > 300:
            return cached[0]
        body = (
            self.app_client()
            .request("POST", f"/app/installations/{installation_id}/access_tokens")
            .json()
        )
        expires = _parse_time(body.get("expires_at")) or self.clock() + 3600
        self._tokens[installation_id] = (body["token"], expires)
        return str(body["token"])

    def client(self, installation_id: int) -> GitHubClient:
        return GitHubClient(
            self.installation_token(installation_id), self.api_url, transport=self.transport
        )


def _parse_time(value: str | None) -> float | None:
    if not value:
        return None
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        *,
        transport: httpx.BaseTransport | None = None,
        bearer: bool = False,
        retries: int = 3,
        max_wait_s: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.token = token
        self.retries = retries
        self.max_wait_s = max_wait_s
        self.sleep = sleep
        self._http = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}" if bearer else f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            },
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _rate_limit_wait(self, resp: httpx.Response) -> float | None:
        if resp.status_code not in (403, 429):
            return None
        if "retry-after" in resp.headers:
            return float(resp.headers["retry-after"])
        if resp.headers.get("x-ratelimit-remaining") == "0":
            reset = float(resp.headers.get("x-ratelimit-reset", "0"))
            return max(1.0, reset - time.time())
        return None

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        accept: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """One API call; a 304 Not Modified is returned like a success."""
        headers = {**({"Accept": accept} if accept else {}), **(headers or {})} or None
        for attempt in range(self.retries + 1):
            resp = self._http.request(method, path, json=json, params=params, headers=headers)
            wait = self._rate_limit_wait(resp)
            if wait is not None and attempt < self.retries:
                wait = min(wait, self.max_wait_s)
                log.warning("GitHub rate limit on %s %s; waiting %.0f s", method, path, wait)
                self.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise GitHubError(
                    f"{method} {path}: {resp.status_code} {resp.text[:300]}", resp.status_code
                )
            return resp
        raise GitHubError(f"{method} {path}: still rate limited after {self.retries} retries", 429)

    # The calls PRism makes.

    def pull(self, repo: str, number: int) -> dict[str, Any]:
        data: dict[str, Any] = self.request("GET", f"/repos/{repo}/pulls/{number}").json()
        return data

    def pull_diff(self, repo: str, number: int) -> str:
        return self.request(
            "GET", f"/repos/{repo}/pulls/{number}", accept="application/vnd.github.diff"
        ).text

    def open_pulls(self, repo: str) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self.request(
            "GET", f"/repos/{repo}/pulls", params={"state": "open", "per_page": 50}
        ).json()
        return data

    def installation_repos(self) -> list[str]:
        data = self.request("GET", "/installation/repositories", params={"per_page": 100}).json()
        return [r["full_name"] for r in data.get("repositories", [])]

    def create_review(
        self,
        repo: str,
        number: int,
        *,
        commit_id: str,
        body: str,
        comments: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        data: dict[str, Any] = self.request(
            "POST",
            f"/repos/{repo}/pulls/{number}/reviews",
            json={
                "commit_id": commit_id,
                "body": body,
                "event": "COMMENT",
                "comments": list(comments),
            },
        ).json()
        return data

    def comment_reactions(
        self, repo: str, comment_id: int, etag: str | None = None
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        """Reactions on a review comment, and the response's ETag. With the ETag of the last
        call, an unchanged list comes back as 304 (None here), which costs no rate limit."""
        resp = self.request(
            "GET",
            f"/repos/{repo}/pulls/comments/{comment_id}/reactions",
            params={"per_page": 100},
            headers={"If-None-Match": etag} if etag else None,
        )
        if resp.status_code == 304:
            return None, etag
        data: list[dict[str, Any]] = resp.json()
        return data, resp.headers.get("etag")

    def review_comments(self, repo: str, number: int, review_id: int) -> list[dict[str, Any]]:
        """The comments of one review, with their lines.

        Read from the PR's comment list, not `/reviews/{id}/comments`: that endpoint returns
        `line: null` (only the old diff `position`), so comments cannot be matched to findings.
        """
        data: list[dict[str, Any]] = self.request(
            "GET", f"/repos/{repo}/pulls/{number}/comments", params={"per_page": 100}
        ).json()
        return [c for c in data if c.get("pull_request_review_id") == review_id]


def installations(auth: AppAuth) -> list[int]:
    client = auth.app_client()
    try:
        return [int(i["id"]) for i in client.request("GET", "/app/installations").json()]
    finally:
        client.close()
