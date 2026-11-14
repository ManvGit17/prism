"""Fakes shared by the review pipeline tests: diffs, a fixed-score gate and a reviewer model."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from prism.retrieval.diff import parse_diff
from prism.review.plan import HunkTask


def hunk_diff(path: str, start: int, added: Sequence[str]) -> str:
    body = "\n".join(f"+{a}" for a in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -{start},1 +{start},{len(added) + 1} @@\n context_line = 1\n{body}\n"
    )


def many_files(n: int, hunks_per_file: int = 1) -> str:
    parts = []
    for i in range(n):
        for h in range(hunks_per_file):
            parts.append(
                hunk_diff(f"pkg/mod{i}.py", 10 + 20 * h, [f"value_{i}_{h} = compute({h})"])
            )
    # git puts all hunks of one file under one header; repeated headers parse as one file each,
    # which is fine for these tests.
    return "".join(parts)


class ScoreGate:
    name = "fixed"

    def __init__(self, threshold: float, scores: dict[str, float]) -> None:
        self.threshold = threshold
        self.scores = scores

    def score(self, hunks: Sequence[str]) -> list[float]:
        return [next((v for k, v in self.scores.items() if k in h), 0.0) for h in hunks]

    def needs_review(self, hunks: Sequence[str]) -> list[bool]:
        return [s >= self.threshold for s in self.score(hunks)]


class FakeModel:
    """Replies with a comment naming the first added identifier; tracks concurrency."""

    name = "fake-reviewer"

    def __init__(self, replies: dict[str, list[str]] | None = None, delay: float = 0.01) -> None:
        self.replies = replies or {}
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    async def reply(self, hunk: str, attempt: int) -> str:
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        for key, seq in self.replies.items():
            if key in hunk:
                return seq[min(attempt, len(seq) - 1)]
        added = next(ln[1:] for ln in hunk.splitlines() if ln.startswith("+"))
        name = added.split("=")[0].strip()
        return json.dumps({"comment": f"`{name}` is computed but never used after this line."})


def task_for(diff: str) -> HunkTask:
    f = parse_diff(diff)[0]
    return HunkTask(f.path, 0, f.hunks[0], 1.0, False, 1.0)
