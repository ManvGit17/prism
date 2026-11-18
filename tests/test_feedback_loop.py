"""The feedback loop: reports and exports from rated comments."""

from __future__ import annotations

import json
from pathlib import Path

from prism.github.feedback_loop import export, rated, render, report, suggest_threshold
from prism.store.reviews import Feedback, InMemoryReviewStore, StoredFinding, StoredReview


def finding(comment: str, hunk: str, author: str, conf: float, category: str) -> StoredFinding:
    return StoredFinding(
        path="a.py",
        line=3,
        hunk_header="@@ -1 +1 @@",
        hunk=hunk,
        author=author,
        comment=comment,
        posted=True,
        decision="kept",
        confidence=conf,
        category=category,
    )


def store_with_feedback() -> InMemoryReviewStore:
    store = InMemoryReviewStore()
    review = store.save(
        StoredReview(
            key="k",
            repo="o/r",
            pr_number=3,
            head_sha="abc",
            status="complete",
            findings=(
                finding("off by one in the loop bound", "H1", "claude", 0.9, "bug"),
                finding("rename x to count", "H1", "prism-reviewer:v2", 0.62, "style"),
                finding("missing await on close()", "H2", "claude", 0.85, "bug"),
                finding("never rated", "H3", "bandit", 0.7, "security"),
            ),
        )
    )
    store.mark_posted(review.id, 1, {f.id: 100 + i for i, f in enumerate(review.findings)})
    ids = [f.id for f in store.get(review.id).findings]  # type: ignore[union-attr]
    store.save_feedback(Feedback(finding_id=ids[0], thumbs_up=2))
    store.save_feedback(Feedback(finding_id=ids[1], thumbs_down=1))
    store.save_feedback(Feedback(finding_id=ids[2], resolved=True))
    return store


def test_report_counts_useful_comments_by_author_and_suggests_a_threshold() -> None:
    rows = rated(store_with_feedback())
    assert len(rows) == 3, "the comment nobody rated is left out"
    rep = report(rows)
    assert rep["overall"] == {"rated": 3, "useful": 2, "share": 0.667}
    assert rep["author"]["claude"]["share"] == 1.0
    assert rep["author"]["prism-reviewer:v2"]["useful"] == 0
    assert suggest_threshold(rows) == 0.7  # dropping the 0.62 style comment reaches 100%
    assert "Useful: 2 (67%)" in render(rep, 90)


def test_export_writes_rows_and_preference_pairs(tmp_path: Path) -> None:
    items, pairs = export(rated(store_with_feedback()), tmp_path / "feedback.jsonl")
    assert (items, pairs) == (3, 1)
    (pair,) = [json.loads(x) for x in (tmp_path / "feedback.pairs.jsonl").read_text().splitlines()]
    assert pair == {
        "prompt": "H1",
        "chosen": "off by one in the loop bound",
        "rejected": "rename x to count",
    }
