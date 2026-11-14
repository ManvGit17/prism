"""Stored reviews: what `prism review` decided and why (Phase 6, ADR 0015).

The MCP tools (`explain_finding`, `review_history`), the GitHub App and the dashboard all read
reviews back, so every review is saved with every verifier decision, not only the comments that
were posted: a dropped comment is part of the answer to "why did PRism say nothing here?".

A review is keyed by `repo#pr#head_sha#diff_hash`. Saving the same key again replaces the
review and its findings, so a rerun of a handoff review (first `awaiting_verification`, then
`complete`) ends as one row. `InMemoryReviewStore` and `PostgresReviewStore` pass the same
contract tests.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from prism.review.checks import Decision
from prism.schemas import Evidence, PRReview

Status = Literal["awaiting_verification", "complete"]


class StoredFinding(BaseModel):
    """One comment the pipeline considered, posted or not."""

    model_config = ConfigDict(frozen=True)

    id: int = 0  # set by the store
    path: str
    line: int
    hunk_header: str
    hunk: str
    author: str  # the reviewer model, a SAST tool or the frontier model
    comment: str
    posted: bool  # kept by the verifier and within the comment cap
    decision: str  # "kept", or why it was dropped
    verifier_note: str = ""
    severity: str | None = None
    category: str | None = None
    confidence: float | None = None
    evidence: tuple[Evidence, ...] = ()
    suggested_fix: str | None = None
    tools: tuple[str, ...] = ()  # SAST findings in the same hunk
    github_comment_id: int | None = None


class StoredReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int = 0  # set by the store
    key: str
    repo: str
    pr_number: int | None
    head_sha: str
    status: Status
    verdict: str | None = None
    summary: str = ""
    cost_usd: float = 0.0
    duration_s: float = 0.0
    trace_id: str | None = None
    work_dir: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    findings: tuple[StoredFinding, ...] = ()


def review_key(repo: str, pr_number: int | None, head_sha: str, diff_text: str) -> str:
    digest = hashlib.sha1(diff_text.encode("utf-8")).hexdigest()[:12]
    return f"{repo}#{pr_number or 'local'}#{head_sha}#{digest}"


def findings_from(
    decisions: Sequence[Decision],
    review: PRReview,
    tools: Mapping[tuple[str, str], Sequence[str]],
) -> tuple[StoredFinding, ...]:
    """Every decision as a finding; `posted` is true for the comments in the final review."""
    posted = {(c.path, c.line, c.comment) for c in review.comments}
    out = []
    for d in decisions:
        c, kept = d.candidate, d.kept
        out.append(
            StoredFinding(
                path=c.path,
                line=c.raw.line,
                hunk_header=c.task.hunk.header,
                hunk=c.task.text,
                author=c.model,
                comment=c.raw.comment,
                posted=kept is not None and (kept.path, kept.line, kept.comment) in posted,
                decision=d.reason,
                verifier_note=d.note,
                severity=kept.severity.value if kept else None,
                category=kept.category.value if kept else None,
                confidence=kept.confidence if kept else None,
                evidence=kept.evidence if kept else (),
                suggested_fix=kept.suggested_fix if kept else None,
                tools=tuple(tools.get((c.path, c.task.hunk.header), ())),
            )
        )
    return tuple(out)


def stored_review(
    review: PRReview,
    *,
    key: str,
    status: Status,
    decisions: Sequence[Decision] = (),
    tools: Mapping[tuple[str, str], Sequence[str]] | None = None,
    trace_id: str | None = None,
    work_dir: str | None = None,
) -> StoredReview:
    return StoredReview(
        key=key,
        repo=review.repo,
        pr_number=review.pr_number,
        head_sha=review.head_sha,
        status=status,
        verdict=review.verdict.value if status == "complete" else None,
        summary=review.summary,
        cost_usd=review.cost_usd,
        duration_s=review.duration_s,
        trace_id=trace_id,
        work_dir=work_dir,
        findings=findings_from(decisions, review, tools or {}) if status == "complete" else (),
    )


class ReviewStore(Protocol):
    def save(self, review: StoredReview) -> StoredReview:
        """Insert or replace by key. Returns the review with ids set."""
        ...

    def get(self, review_id: int) -> StoredReview | None: ...

    def finding(self, finding_id: int) -> tuple[StoredReview, StoredFinding] | None: ...

    def history(
        self, repo: str | None = None, pr_number: int | None = None, limit: int = 20
    ) -> list[StoredReview]:
        """Newest first, findings included."""
        ...

    def close(self) -> None: ...


class InMemoryReviewStore:
    def __init__(self) -> None:
        self._reviews: dict[str, StoredReview] = {}
        self._next_review = 1
        self._next_finding = 1

    def save(self, review: StoredReview) -> StoredReview:
        old = self._reviews.get(review.key)
        rid = old.id if old else self._next_review
        if old is None:
            self._next_review += 1
        findings = []
        for f in review.findings:
            findings.append(f.model_copy(update={"id": self._next_finding}))
            self._next_finding += 1
        update: dict[str, Any] = {"id": rid, "findings": tuple(findings)}
        if old is not None:
            update["created_at"] = old.created_at
        saved = review.model_copy(update=update)
        self._reviews[review.key] = saved
        return saved

    def get(self, review_id: int) -> StoredReview | None:
        return next((r for r in self._reviews.values() if r.id == review_id), None)

    def finding(self, finding_id: int) -> tuple[StoredReview, StoredFinding] | None:
        for r in self._reviews.values():
            for f in r.findings:
                if f.id == finding_id:
                    return r, f
        return None

    def history(
        self, repo: str | None = None, pr_number: int | None = None, limit: int = 20
    ) -> list[StoredReview]:
        rows = [
            r
            for r in self._reviews.values()
            if (repo is None or r.repo == repo) and (pr_number is None or r.pr_number == pr_number)
        ]
        return sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True)[:limit]

    def close(self) -> None:
        return None


_REVIEW_COLS = (
    "id, key, repo, pr_number, head_sha, status, verdict, summary, cost_usd, duration_s, "
    "trace_id, work_dir, created_at"
)
_FINDING_COLS = (
    "id, path, line, hunk_header, hunk, author, comment, posted, decision, verifier_note, "
    "severity, category, confidence, evidence, suggested_fix, tools, github_comment_id"
)


class PostgresReviewStore:
    """Reviews in the `reviews` and `review_findings` tables (migration 2)."""

    def __init__(self, conn: Any) -> None:
        from prism.store.postgres import migrate

        self._conn = conn
        migrate(conn)

    @classmethod
    def from_settings(cls) -> PostgresReviewStore:
        from prism.store.postgres import connect

        return cls(connect())

    def close(self) -> None:
        self._conn.close()

    def save(self, review: StoredReview) -> StoredReview:
        with self._conn.transaction():
            row = self._conn.execute(
                """
                INSERT INTO reviews (key, repo, pr_number, head_sha, status, verdict, summary,
                                     cost_usd, duration_s, trace_id, work_dir)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    status = EXCLUDED.status, verdict = EXCLUDED.verdict,
                    summary = EXCLUDED.summary, cost_usd = EXCLUDED.cost_usd,
                    duration_s = EXCLUDED.duration_s, trace_id = EXCLUDED.trace_id,
                    work_dir = EXCLUDED.work_dir, updated_at = now()
                RETURNING id
                """,
                (
                    review.key,
                    review.repo,
                    review.pr_number,
                    review.head_sha,
                    review.status,
                    review.verdict,
                    review.summary,
                    review.cost_usd,
                    review.duration_s,
                    review.trace_id,
                    review.work_dir,
                ),
            ).fetchone()
            rid = int(row[0])
            self._conn.execute("DELETE FROM review_findings WHERE review_id = %s", (rid,))
            for f in review.findings:
                self._conn.execute(
                    f"""
                    INSERT INTO review_findings (review_id, {_FINDING_COLS.removeprefix("id, ")})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        rid,
                        f.path,
                        f.line,
                        f.hunk_header,
                        f.hunk,
                        f.author,
                        f.comment,
                        f.posted,
                        f.decision,
                        f.verifier_note,
                        f.severity,
                        f.category,
                        f.confidence,
                        json.dumps([e.model_dump() for e in f.evidence]),
                        f.suggested_fix,
                        json.dumps(list(f.tools)),
                        f.github_comment_id,
                    ),
                )
        saved = self.get(rid)
        assert saved is not None
        return saved

    def _findings(self, review_id: int) -> tuple[StoredFinding, ...]:
        rows = self._conn.execute(
            f"SELECT {_FINDING_COLS} FROM review_findings WHERE review_id = %s ORDER BY id",
            (review_id,),
        ).fetchall()
        keys = _FINDING_COLS.split(", ")
        out = []
        for row in rows:
            data = dict(zip(keys, row, strict=True))
            data["evidence"] = tuple(Evidence(**e) for e in _json(data["evidence"]))
            data["tools"] = tuple(_json(data["tools"]))
            out.append(StoredFinding.model_validate(data))
        return tuple(out)

    def _review(self, row: tuple[Any, ...]) -> StoredReview:
        data = dict(zip(_REVIEW_COLS.split(", "), row, strict=True))
        data["findings"] = self._findings(int(data["id"]))
        return StoredReview.model_validate(data)

    def get(self, review_id: int) -> StoredReview | None:
        row = self._conn.execute(
            f"SELECT {_REVIEW_COLS} FROM reviews WHERE id = %s", (review_id,)
        ).fetchone()
        return None if row is None else self._review(row)

    def finding(self, finding_id: int) -> tuple[StoredReview, StoredFinding] | None:
        row = self._conn.execute(
            "SELECT review_id FROM review_findings WHERE id = %s", (finding_id,)
        ).fetchone()
        if row is None:
            return None
        review = self.get(int(row[0]))
        if review is None:
            return None
        found = next((f for f in review.findings if f.id == finding_id), None)
        return None if found is None else (review, found)

    def history(
        self, repo: str | None = None, pr_number: int | None = None, limit: int = 20
    ) -> list[StoredReview]:
        rows = self._conn.execute(
            f"""
            SELECT {_REVIEW_COLS} FROM reviews
            WHERE (%(repo)s::text IS NULL OR repo = %(repo)s)
              AND (%(pr)s::int IS NULL OR pr_number = %(pr)s)
            ORDER BY created_at DESC, id DESC LIMIT %(limit)s
            """,
            {"repo": repo, "pr": pr_number, "limit": limit},
        ).fetchall()
        return [self._review(r) for r in rows]

    def delete_repo(self, repo: str) -> None:
        """For tests: remove a repo's reviews (findings cascade)."""
        self._conn.execute("DELETE FROM reviews WHERE repo = %s", (repo,))


def _json(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        loaded: list[Any] = json.loads(value)
        return loaded
    return list(value)
