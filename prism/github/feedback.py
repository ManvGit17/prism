"""Feedback on PRism's comments: reactions, replies and resolved threads (ADR 0019).

    every 15 min (beat): for comments posted in the last 14 days, read 👍/👎 reactions with the
                         ETag of the last read (304 Not Modified costs no rate limit)
    webhooks:            a reply to a PRism comment, a thread resolved or reopened
    then:                changed feedback -> a Langfuse score on the review's trace, and an item
                         in the "prism-comment-feedback" dataset, labelled good / bad / mixed

The labelled comments are what a DPO round needs: the same hunk with a comment people liked and
one they did not. Bots' reactions are ignored, and so are PRism's own.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from prism.github.api import AppAuth, GitHubClient
from prism.store.reviews import Feedback, ReviewStore, StoredFinding, StoredReview

log = logging.getLogger(__name__)


def is_human(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    return user.get("type") != "Bot" and not str(user.get("login", "")).endswith("[bot]")


def count_reactions(reactions: list[dict[str, Any]]) -> tuple[int, int]:
    up = sum(1 for r in reactions if r.get("content") == "+1" and is_human(r.get("user")))
    down = sum(1 for r in reactions if r.get("content") == "-1" and is_human(r.get("user")))
    return up, down


def score_value(fb: Feedback) -> float:
    """-1 (all thumbs down) to 1 (all thumbs up); a resolved thread with no reactions is 1."""
    votes = fb.thumbs_up + fb.thumbs_down
    if votes:
        return (fb.thumbs_up - fb.thumbs_down) / votes
    return 1.0 if fb.resolved else 0.0


def signature(fb: Feedback) -> str:
    return json.dumps(fb.signal)


class FeedbackSink(Protocol):
    def send(self, review: StoredReview, finding: StoredFinding, fb: Feedback) -> None: ...

    def flush(self) -> None: ...


class LangfuseFeedbackSink:
    def __init__(self, client: Any, dataset: str) -> None:
        self.client = client
        self.dataset = dataset
        self._dataset_ready = False

    def _ensure_dataset(self) -> None:
        if self._dataset_ready:
            return
        try:
            self.client.create_dataset(
                name=self.dataset,
                description="PRism review comments people reacted to (good / bad / mixed), "
                "for preference training.",
            )
        except Exception as exc:  # already exists, or the server is older
            log.debug("create_dataset: %s", exc)
        self._dataset_ready = True

    def send(self, review: StoredReview, finding: StoredFinding, fb: Feedback) -> None:
        key = f"prism-finding-{finding.id}"
        detail = (
            f"{fb.label}: +{fb.thumbs_up} -{fb.thumbs_down}, {fb.replies} replies, "
            f"resolved={fb.resolved}"
        )
        meta = {
            "finding_id": finding.id,
            "github_comment_id": finding.github_comment_id,
            "repo": review.repo,
            "pr": review.pr_number,
            "path": finding.path,
            "line": finding.line,
            "author": finding.author,
        }
        if review.trace_id:
            self.client.create_score(
                name="comment_feedback",
                value=score_value(fb),
                trace_id=review.trace_id,
                score_id=key,
                data_type="NUMERIC",
                comment=detail,
                metadata=meta,
            )
        self._ensure_dataset()
        self.client.create_dataset_item(
            dataset_name=self.dataset,
            id=key,
            input={"path": finding.path, "line": finding.line, "hunk": finding.hunk},
            expected_output={"comment": finding.comment},
            metadata=meta
            | {
                "label": fb.label,
                "thumbs_up": fb.thumbs_up,
                "thumbs_down": fb.thumbs_down,
                "replies": fb.replies,
                "resolved": fb.resolved,
                "severity": finding.severity,
                "category": finding.category,
                "confidence": finding.confidence,
            },
            source_trace_id=review.trace_id,
        )

    def flush(self) -> None:
        self.client.flush()


class FeedbackCollector:
    def __init__(
        self,
        auth: AppAuth,
        store: ReviewStore,
        sink: FeedbackSink | None = None,
        *,
        window_days: int = 14,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.auth = auth
        self.store = store
        self.sink = sink
        self.window_days = window_days
        self.clock = clock

    def collect(self) -> dict[str, int]:
        """Read reactions for recent comments, then send what changed. Returns counts."""
        counts = {"comments": 0, "not_modified": 0, "updated": 0, "sent": 0}
        since = self.clock() - timedelta(days=self.window_days)
        posted = self.store.posted_since(since)
        clients: dict[int, GitHubClient] = {}
        try:
            for review, finding in posted:
                if review.installation_id is None or finding.github_comment_id is None:
                    continue
                counts["comments"] += 1
                inst = review.installation_id
                if inst not in clients:
                    clients[inst] = self.auth.client(inst)
                fb = self.store.get_feedback(finding.id) or Feedback(finding_id=finding.id)
                reactions, etag = clients[inst].comment_reactions(
                    review.repo, finding.github_comment_id, fb.etag
                )
                if reactions is None:
                    counts["not_modified"] += 1
                    fb = fb.model_copy(update={"checked_at": self.clock()})
                else:
                    up, down = count_reactions(reactions)
                    if (up, down) != (fb.thumbs_up, fb.thumbs_down):
                        counts["updated"] += 1
                    fb = fb.model_copy(
                        update={
                            "thumbs_up": up,
                            "thumbs_down": down,
                            "etag": etag,
                            "checked_at": self.clock(),
                        }
                    )
                self.store.save_feedback(fb)
        finally:
            for c in clients.values():
                c.close()
        counts["sent"] = self.send_changes(posted)
        return counts

    def send_changes(self, posted: list[tuple[StoredReview, StoredFinding]]) -> int:
        if self.sink is None:
            return 0
        sent = 0
        for review, finding in posted:
            fb = self.store.get_feedback(finding.id)
            if fb is None or fb.label == "none" or fb.sent == signature(fb):
                continue
            self.sink.send(review, finding, fb)
            self.store.save_feedback(fb.model_copy(update={"sent": signature(fb)}))
            sent += 1
        if sent:
            self.sink.flush()
        return sent


def on_review_comment(store: ReviewStore, event: dict[str, Any]) -> bool:
    """A reply to a PRism comment (`pull_request_review_comment`, created). True if counted."""
    comment = event.get("comment") or {}
    parent = comment.get("in_reply_to_id")
    if event.get("action") != "created" or not parent or not is_human(comment.get("user")):
        return False
    finding_id = store.finding_by_comment(int(parent))
    if finding_id is None:
        return False
    fb = store.get_feedback(finding_id) or Feedback(finding_id=finding_id)
    store.save_feedback(fb.model_copy(update={"replies": fb.replies + 1}))
    return True


def on_review_thread(store: ReviewStore, event: dict[str, Any]) -> bool:
    """A thread with a PRism comment resolved or reopened (`pull_request_review_thread`)."""
    action = event.get("action")
    if action not in ("resolved", "unresolved"):
        return False
    changed = False
    for c in (event.get("thread") or {}).get("comments", []):
        finding_id = store.finding_by_comment(int(c["id"]))
        if finding_id is None:
            continue
        fb = store.get_feedback(finding_id) or Feedback(finding_id=finding_id)
        store.save_feedback(fb.model_copy(update={"resolved": action == "resolved"}))
        changed = True
    return changed
