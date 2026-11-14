"""Stored reviews: the same contract for memory and Postgres, and what a real review stores."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from repo_ids import repo_id
from review_fakes import FakeModel, hunk_diff

from prism.models.gate import PassAllGate
from prism.review.checks import RuleVerifier
from prism.review.graph import ReviewOptions, run_review
from prism.schemas import Evidence
from prism.store.reviews import (
    InMemoryReviewStore,
    PostgresReviewStore,
    ReviewStore,
    StoredFinding,
    StoredReview,
    review_key,
    stored_review,
)


@pytest.fixture(params=[pytest.param("memory"), pytest.param("postgres", marks=pytest.mark.smoke)])
def store(request: pytest.FixtureRequest) -> Iterator[ReviewStore]:
    if request.param == "memory":
        yield InMemoryReviewStore()
        return
    pg = PostgresReviewStore.from_settings()
    try:
        yield pg
    finally:
        for name in ("a", "b"):
            pg.delete_repo(repo_id(f"reviews-{name}"))
        pg.close()


def finding(comment: str, *, posted: bool = True) -> StoredFinding:
    return StoredFinding(
        path="app/x.py",
        line=3,
        hunk_header="@@ -1,1 +1,3 @@",
        hunk="@@ -1,1 +1,3 @@\n+x",
        author="prism-reviewer:v2",
        comment=comment,
        posted=posted,
        decision="kept" if posted else "verifier: wrong",
        verifier_note="checked line 3",
        severity="major" if posted else None,
        category="bug" if posted else None,
        confidence=0.9 if posted else None,
        evidence=(Evidence(source="hunk", detail="line 3"),) if posted else (),
        tools=("bandit B602 (high) line 3: shell=True",),
    )


def review(
    repo: str, pr: int, sha: str, *findings: StoredFinding, status: str = "complete"
) -> StoredReview:
    return StoredReview(
        key=f"{repo}#{pr}#{sha}#d",
        repo=repo,
        pr_number=pr,
        head_sha=sha,
        status=status,
        verdict="medium" if status == "complete" else None,
        summary="s",
        findings=findings,
    )


def test_save_get_and_finding(store: ReviewStore) -> None:
    repo = repo_id("reviews-a")
    saved = store.save(review(repo, 7, "abc", finding("bug here"), finding("nope", posted=False)))
    assert saved.id > 0 and [f.id > 0 for f in saved.findings] == [True, True]
    got = store.get(saved.id)
    assert got is not None and got.findings == saved.findings
    assert got.findings[0].evidence[0].detail == "line 3"
    assert got.findings[0].tools == ("bandit B602 (high) line 3: shell=True",)
    found = store.finding(saved.findings[1].id)
    assert found is not None and found[0].id == saved.id and found[1].comment == "nope"
    assert store.finding(10**9) is None and store.get(10**9) is None


def test_same_key_replaces_the_review(store: ReviewStore) -> None:
    repo = repo_id("reviews-a")
    first = store.save(review(repo, 7, "abc", status="awaiting_verification"))
    done = store.save(review(repo, 7, "abc", finding("bug here")))
    assert done.id == first.id and done.status == "complete" and len(done.findings) == 1
    assert len(store.history(repo)) == 1


def test_history_filters_and_orders(store: ReviewStore) -> None:
    a, b = repo_id("reviews-a"), repo_id("reviews-b")
    store.save(review(a, 1, "s1"))
    store.save(review(a, 2, "s2"))
    store.save(review(b, 1, "s3"))
    assert [r.head_sha for r in store.history(a)] == ["s2", "s1"]
    assert [r.head_sha for r in store.history(a, 1)] == ["s1"]
    assert [r.head_sha for r in store.history(b)] == ["s3"]
    assert len(store.history(a, limit=1)) == 1


def test_a_real_review_stores_every_decision(tmp_path: Path) -> None:
    diff = hunk_diff("a.py", 1, ["total = price * qty"]) + hunk_diff("b.py", 1, ["Why?"])
    result, state = asyncio.run(
        run_review(
            tmp_path,
            diff,
            gate=PassAllGate(),
            model=FakeModel(replies={"Why?": ['{"comment": "Why?"}']}),
            verifier=RuleVerifier(),
            options=ReviewOptions(),
            repo_name="demo",
            pr_number=3,
        )
    )
    key = review_key("demo", 3, result.head_sha, diff)
    rec = stored_review(
        result,
        key=key,
        status="complete",
        decisions=state["decisions"],
        tools=state.get("tools", {}),
    )
    by_path = {f.path: f for f in rec.findings}
    assert by_path["a.py"].posted and by_path["a.py"].decision == "kept"
    assert not by_path["b.py"].posted and by_path["b.py"].decision == "vague"
    assert by_path["a.py"].hunk.startswith("@@") and rec.verdict == result.verdict.value
    saved = InMemoryReviewStore().save(rec)
    assert saved.key == key and len(saved.findings) == 2

    pending = stored_review(
        result, key=key, status="awaiting_verification", decisions=state["decisions"]
    )
    assert pending.findings == () and pending.verdict is None
