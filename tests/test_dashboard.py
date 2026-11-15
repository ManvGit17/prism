"""The dashboard API over an in-memory store."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from prism.dashboard import DashboardData, author_group, create_dashboard, routing
from prism.store.reviews import (
    Feedback,
    InMemoryReviewStore,
    StoredFinding,
    StoredReview,
)


def finding(author: str, posted: bool, comment: str = "c") -> StoredFinding:
    return StoredFinding(
        path="shop/x.py",
        line=3,
        hunk_header="@@",
        hunk="+x",
        author=author,
        comment=comment,
        posted=posted,
        decision="kept" if posted else "verifier: wrong",
        severity="major" if posted else None,
        category="bug" if posted else None,
        confidence=0.9 if posted else None,
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    store = InMemoryReviewStore()
    a = store.save(
        StoredReview(
            key="a",
            repo="acme/shop",
            pr_number=2,
            head_sha="a" * 40,
            status="complete",
            verdict="high",
            cost_usd=0.02,
            duration_s=30.0,
            summary="Risk: high.\nRouting: 1 hunk(s) to prism-reviewer:v2.\n"
            "Escalated to claude: 2 hunk(s) (security 2).",
            findings=(
                finding("claude", True, "sql"),
                finding("bandit", True, "pickle"),
                finding("prism-reviewer:v2", False),
            ),
        )
    )
    store.mark_posted(a.id, 1, {a.findings[0].id: 10, a.findings[1].id: 11}, 7)
    store.save_feedback(Feedback(finding_id=a.findings[0].id, thumbs_up=2))
    store.save_feedback(Feedback(finding_id=a.findings[1].id, thumbs_down=1))
    store.save(
        StoredReview(
            key="b",
            repo="acme/shop",
            pr_number=3,
            head_sha="b" * 40,
            status="awaiting_verification",
        )
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "sft.md").write_text("# Reviewer fine-tuning\n\nbody\n", encoding="utf-8")
    return TestClient(create_dashboard(DashboardData(store, reports), static_dir=None))


def test_summary(client: TestClient) -> None:
    s = client.get("/api/summary").json()
    assert (s["reviews"], s["complete"], s["awaiting_verification"], s["on_github"]) == (2, 1, 1, 1)
    assert (s["comments_posted"], s["comments_considered"]) == (2, 3)
    assert s["accept_rate"] == 0.5 and s["rated"] == 2
    assert s["authors"] == {"local model": 0, "static analysis": 1, "Claude": 1}
    assert s["routing"] == {"local": 1, "claude": 2}
    assert s["median_duration_s"] == 30.0 and s["cost_usd"] == 0.02


def test_reviews_and_detail(client: TestClient) -> None:
    rows = client.get("/api/reviews").json()
    assert [r["pr_number"] for r in rows] == [3, 2]
    done = rows[1]
    assert done["pr_url"] == "https://github.com/acme/shop/pull/2"
    assert (done["posted"], done["considered"], done["good"], done["bad"]) == (2, 3, 1, 1)
    detail = client.get(f"/api/reviews/{done['id']}").json()
    by_comment = {f["comment"]: f for f in detail["findings"]}
    assert by_comment["sql"]["feedback"]["label"] == "good"
    assert by_comment["pickle"]["group"] == "static analysis"
    assert by_comment["c"]["posted"] is False and by_comment["c"]["feedback"] is None
    assert client.get("/api/reviews/999").status_code == 404


def test_reports(client: TestClient) -> None:
    assert client.get("/api/reports").json() == [{"name": "sft", "title": "Reviewer fine-tuning"}]
    assert client.get("/api/reports/sft").text.startswith("# Reviewer")
    assert client.get("/api/reports/missing").status_code == 404
    assert client.get("/api/reports/..%2F..%2Fsecret").status_code == 404


def test_unbuilt_page_says_how_to_build(client: TestClient) -> None:
    assert "npm ci" in client.get("/").text


def test_helpers() -> None:
    assert author_group("claude") == "Claude" and author_group("semgrep") == "static analysis"
    assert author_group("qwen2.5-coder:3b") == "local model"
    assert routing("no routing lines") == {"local": 0, "claude": 0}
