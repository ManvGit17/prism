"""The dashboard's JSON API and the built page (ADR 0020).

    prism dashboard --port 8090     ->  http://127.0.0.1:8090

Read-only views over the review store: every review with its cost, latency, verdict, comments
posted out of comments considered, feedback, and how its hunks were routed; one review's findings
with the verifier's reasons; totals; and the eval reports in `eval/reports`. The React page in
`dashboard/` is built into `dashboard/dist` and served from `/`.

No `from __future__ import annotations`: FastAPI reads endpoint annotations at runtime.
"""

import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from prism.store.reviews import ReviewStore, StoredFinding, StoredReview

TOOLS = frozenset({"bandit", "semgrep", "ruff", "pmd", "prism"})
_ROUTED_LOCAL = re.compile(r"Routing: (\d+) hunk")
_ROUTED_UP = re.compile(r"Escalated to [^:]+: (\d+) hunk")


def author_group(author: str) -> str:
    if author in TOOLS:
        return "static analysis"
    if author.startswith("claude"):
        return "Claude"
    return "local model"


def routing(summary: str) -> dict[str, int]:
    """Hunks the review sent to the local model and to Claude, from its summary lines."""
    local = _ROUTED_LOCAL.search(summary)
    up = _ROUTED_UP.search(summary)
    return {"local": int(local.group(1)) if local else 0, "claude": int(up.group(1)) if up else 0}


def pr_url(r: StoredReview, web: str = "https://github.com") -> str | None:
    if r.pr_number is None or "/" not in r.repo:
        return None
    return f"{web}/{r.repo}/pull/{r.pr_number}"


class DashboardData:
    def __init__(self, store: ReviewStore, reports_dir: Path) -> None:
        self.store = store
        self.reports_dir = reports_dir

    def _feedback(self, f: StoredFinding) -> dict[str, Any] | None:
        fb = self.store.get_feedback(f.id) if f.posted else None
        if fb is None:
            return None
        return {
            "thumbs_up": fb.thumbs_up,
            "thumbs_down": fb.thumbs_down,
            "replies": fb.replies,
            "resolved": fb.resolved,
            "label": fb.label,
        }

    def _row(self, r: StoredReview) -> dict[str, Any]:
        posted = [f for f in r.findings if f.posted]
        labels = Counter((self._feedback(f) or {}).get("label", "none") for f in posted)
        return {
            "id": r.id,
            "repo": r.repo,
            "pr_number": r.pr_number,
            "pr_url": pr_url(r),
            "head_sha": r.head_sha,
            "status": r.status,
            "verdict": r.verdict,
            "created_at": r.created_at.isoformat(),
            "posted_at": r.posted_at.isoformat() if r.posted_at else None,
            "on_github": r.github_review_id is not None,
            "posted": len(posted),
            "considered": len(r.findings),
            "good": labels["good"],
            "bad": labels["bad"],
            "cost_usd": r.cost_usd,
            "duration_s": r.duration_s,
            "routing": routing(r.summary),
            "authors": dict(Counter(author_group(f.author) for f in posted)),
        }

    def reviews(self, repo: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return [self._row(r) for r in self.store.history(repo, limit=limit)]

    def review(self, review_id: int) -> dict[str, Any] | None:
        r = self.store.get(review_id)
        if r is None:
            return None
        return self._row(r) | {
            "summary": r.summary,
            "findings": [
                {
                    "id": f.id,
                    "path": f.path,
                    "line": f.line,
                    "author": f.author,
                    "group": author_group(f.author),
                    "comment": f.comment,
                    "posted": f.posted,
                    "decision": f.decision,
                    "verifier_note": f.verifier_note,
                    "severity": f.severity,
                    "category": f.category,
                    "confidence": f.confidence,
                    "suggested_fix": f.suggested_fix,
                    "feedback": self._feedback(f),
                }
                for f in r.findings
            ],
        }

    def summary(self, limit: int = 500) -> dict[str, Any]:
        rows = self.reviews(limit=limit)
        done = [r for r in rows if r["status"] == "complete"]
        posted = sum(r["posted"] for r in done)
        considered = sum(r["considered"] for r in done)
        good = sum(r["good"] for r in done)
        bad = sum(r["bad"] for r in done)
        authors: Counter[str] = Counter()
        routed: Counter[str] = Counter()
        for r in done:
            authors.update(r["authors"])
            routed.update(r["routing"])
        durations = [r["duration_s"] for r in done if r["duration_s"]]
        return {
            "reviews": len(rows),
            "complete": len(done),
            "awaiting_verification": len(rows) - len(done),
            "on_github": sum(r["on_github"] for r in rows),
            "comments_posted": posted,
            "comments_considered": considered,
            "kept_rate": posted / considered if considered else None,
            "rated": good + bad,
            "accept_rate": good / (good + bad) if good + bad else None,
            "cost_usd": sum(r["cost_usd"] for r in done),
            "median_duration_s": statistics.median(durations) if durations else None,
            "authors": {k: authors.get(k, 0) for k in ("local model", "static analysis", "Claude")},
            "routing": {"local": routed.get("local", 0), "claude": routed.get("claude", 0)},
        }

    def reports(self) -> list[dict[str, str]]:
        out = []
        for path in sorted(self.reports_dir.glob("*.md")):
            first = path.read_text(encoding="utf-8").splitlines()[:1]
            title = first[0].lstrip("# ").strip() if first else path.stem
            out.append({"name": path.stem, "title": title})
        return out

    def report(self, name: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            return None
        path = self.reports_dir / f"{name}.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None


def create_dashboard(data: DashboardData, static_dir: Path | None = None) -> Any:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, PlainTextResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="PRism dashboard", docs_url=None, redoc_url=None)

    @app.get("/api/summary")
    def summary() -> dict[str, Any]:
        return data.summary()

    @app.get("/api/reviews")
    def reviews(repo: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return data.reviews(repo, min(limit, 500))

    @app.get("/api/reviews/{review_id}")
    def review(review_id: int) -> dict[str, Any]:
        found = data.review(review_id)
        if found is None:
            raise HTTPException(status_code=404, detail="no such review")
        return found

    @app.get("/api/reports")
    def reports() -> list[dict[str, str]]:
        return data.reports()

    @app.get("/api/reports/{name}", response_class=PlainTextResponse)
    def report(name: str) -> str:
        text = data.report(name)
        if text is None:
            raise HTTPException(status_code=404, detail="no such report")
        return text

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="page")
    else:

        @app.get("/", response_class=HTMLResponse)
        def not_built() -> str:
            return (
                "<p>The dashboard page is not built. Run <code>npm ci && npm run build</code> "
                "in <code>dashboard/</code>; the API is under <code>/api</code>.</p>"
            )

    return app
