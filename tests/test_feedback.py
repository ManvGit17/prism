"""Feedback on posted comments: reactions with ETags, replies, resolved threads, Langfuse."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

pytest.importorskip("jwt")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from prism.github.api import AppAuth
from prism.github.feedback import (
    FeedbackCollector,
    LangfuseFeedbackSink,
    count_reactions,
    on_review_comment,
    on_review_thread,
    score_value,
)
from prism.store.reviews import (
    Feedback,
    InMemoryReviewStore,
    StoredFinding,
    StoredReview,
)

REPO = "acme/shop"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
HUMAN = {"login": "dev", "type": "User"}
BOT = {"login": "prism-review-x[bot]", "type": "Bot"}


def reaction(content: str, user: dict[str, str] = HUMAN) -> dict[str, Any]:
    return {"content": content, "user": user}


def test_labels_and_scores() -> None:
    assert Feedback(finding_id=1, thumbs_up=2).label == "good"
    assert Feedback(finding_id=1, thumbs_down=1).label == "bad"
    assert Feedback(finding_id=1, thumbs_up=1, thumbs_down=1).label == "mixed"
    assert Feedback(finding_id=1, resolved=True).label == "good"
    assert Feedback(finding_id=1, replies=3).label == "none"
    assert score_value(Feedback(finding_id=1, thumbs_up=3, thumbs_down=1)) == 0.5
    assert score_value(Feedback(finding_id=1, resolved=True)) == 1.0
    assert score_value(Feedback(finding_id=1)) == 0.0


def test_bot_reactions_do_not_count() -> None:
    got = count_reactions([reaction("+1"), reaction("+1", BOT), reaction("-1"), reaction("eyes")])
    assert got == (1, 1)


def posted_store(posted_at: datetime = NOW - timedelta(days=1)) -> tuple[InMemoryReviewStore, int]:
    store = InMemoryReviewStore()
    saved = store.save(
        StoredReview(
            key="k",
            repo=REPO,
            pr_number=1,
            head_sha="a" * 40,
            status="complete",
            verdict="high",
            trace_id="trace-1",
            findings=(
                StoredFinding(
                    path="x.py",
                    line=2,
                    hunk_header="@@",
                    hunk="+y = x / 0",
                    author="claude",
                    comment="Division by zero.",
                    posted=True,
                    decision="kept",
                    severity="major",
                    category="bug",
                    confidence=0.9,
                ),
            ),
        )
    )
    fid = saved.findings[0].id
    store.mark_posted(saved.id, 900, {fid: 5000}, installation_id=7)
    # Pin the posting time for the 14-day window.
    store._reviews["k"] = store._reviews["k"].model_copy(update={"posted_at": posted_at})
    return store, fid


class FakeReactions:
    def __init__(self) -> None:
        self.reactions = [reaction("+1"), reaction("+1", BOT)]
        self.etag = 'W/"r1"'
        self.calls: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs", "expires_at": "2099-01-01T00:00:00Z"})
        assert re.fullmatch(rf"/repos/{REPO}/pulls/comments/5000/reactions", request.url.path)
        sent = request.headers.get("if-none-match")
        self.calls.append(sent)
        if sent == self.etag:
            return httpx.Response(304)
        return httpx.Response(200, json=self.reactions, headers={"etag": self.etag})


class RecordingSink:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.flushes = 0

    def send(self, review: StoredReview, finding: StoredFinding, fb: Feedback) -> None:
        self.sent.append((finding.id, fb.label))

    def flush(self) -> None:
        self.flushes += 1


@pytest.fixture(scope="module")
def auth_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return str(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
    )


def collector(store: InMemoryReviewStore, gh: FakeReactions, key: str, sink: Any) -> Any:
    auth = AppAuth("1", key, transport=httpx.MockTransport(gh))
    return FeedbackCollector(auth, store, sink, clock=lambda: NOW)


def test_reactions_with_etags_and_send_only_changes(auth_key: str) -> None:
    store, fid = posted_store()
    gh, sink = FakeReactions(), RecordingSink()
    c = collector(store, gh, auth_key, sink)

    first = c.collect()
    assert first == {"comments": 1, "not_modified": 0, "updated": 1, "sent": 1}
    fb = store.get_feedback(fid)
    assert fb is not None and (fb.thumbs_up, fb.thumbs_down, fb.etag) == (1, 0, 'W/"r1"')
    assert sink.sent == [(fid, "good")]

    second = c.collect()
    assert gh.calls == [None, 'W/"r1"'], "the second read is conditional"
    assert second == {"comments": 1, "not_modified": 1, "updated": 0, "sent": 0}

    gh.reactions, gh.etag = [reaction("-1"), reaction("-1")], 'W/"r2"'
    assert c.collect()["sent"] == 1 and sink.sent[-1] == (fid, "bad")


def test_old_reviews_are_not_read(auth_key: str) -> None:
    store, _ = posted_store(posted_at=NOW - timedelta(days=20))
    gh = FakeReactions()
    assert collector(store, gh, auth_key, None).collect()["comments"] == 0 and gh.calls == []


def test_no_sink_still_records_reactions(auth_key: str) -> None:
    store, fid = posted_store()
    assert collector(store, FakeReactions(), auth_key, None).collect()["sent"] == 0
    assert store.get_feedback(fid) is not None


def test_replies_and_resolved_threads() -> None:
    store, fid = posted_store()
    reply = {"action": "created", "comment": {"in_reply_to_id": 5000, "user": HUMAN}}
    assert on_review_comment(store, reply)
    assert not on_review_comment(
        store, {"action": "created", "comment": {"in_reply_to_id": 5000, "user": BOT}}
    )
    assert not on_review_comment(
        store, {"action": "created", "comment": {"in_reply_to_id": 1, "user": HUMAN}}
    )
    assert not on_review_comment(store, {"action": "created", "comment": {"user": HUMAN}})
    thread = {"thread": {"comments": [{"id": 5000}, {"id": 6000}]}}
    assert on_review_thread(store, {"action": "resolved"} | thread)
    fb = store.get_feedback(fid)
    assert fb is not None and fb.replies == 1 and fb.resolved and fb.label == "good"
    assert on_review_thread(store, {"action": "unresolved"} | thread)
    assert store.get_feedback(fid).resolved is False  # type: ignore[union-attr]
    assert not on_review_thread(store, {"action": "edited"} | thread)


class FakeLangfuse:
    def __init__(self) -> None:
        self.scores: list[dict[str, Any]] = []
        self.items: list[dict[str, Any]] = []
        self.datasets: list[str] = []

    def create_score(self, **kw: Any) -> None:
        self.scores.append(kw)

    def create_dataset(self, **kw: Any) -> None:
        self.datasets.append(kw["name"])

    def create_dataset_item(self, **kw: Any) -> None:
        self.items.append(kw)

    def flush(self) -> None:
        return None


def test_langfuse_sink() -> None:
    store, fid = posted_store()
    review = store.history(REPO)[0]
    finding = review.findings[0]
    lf = FakeLangfuse()
    sink = LangfuseFeedbackSink(lf, "prism-comment-feedback")
    fb = Feedback(finding_id=fid, thumbs_up=1, thumbs_down=3)
    sink.send(review, finding, fb)
    sink.send(review, finding, fb)
    (score, _) = lf.scores
    assert score["trace_id"] == "trace-1" and score["score_id"] == f"prism-finding-{fid}"
    assert score["value"] == -0.5 and score["name"] == "comment_feedback"
    assert lf.datasets == ["prism-comment-feedback"], "the dataset is created once"
    item = lf.items[0]
    assert item["id"] == f"prism-finding-{fid}" and item["metadata"]["label"] == "bad"
    assert item["expected_output"] == {"comment": "Division by zero."}

    untraced = review.model_copy(update={"trace_id": None})
    sink.send(untraced, finding, fb)
    assert len(lf.scores) == 2 and len(lf.items) == 3, "no trace: dataset item only"
