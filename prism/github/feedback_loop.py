"""Using the feedback: what people thought of PRism's comments, and data to learn from it.

The App records what people do with every posted comment (reactions, replies, resolved
threads; ADR 0019). This module closes the loop:

* `report`: how often posted comments were judged useful, overall, by author (the local
  reviewer, Claude, each SAST tool), by category and by confidence band, and the lowest
  confidence threshold that would have kept the useful share at or above a target.
* `export`: every rated comment as a JSONL row (the hunk, the comment, its labels and votes),
  and, where the same hunk has a liked and a disliked comment, a preference pair in the
  `prompt` / `chosen` / `rejected` form a DPO round on the reviewer takes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from prism.store.reviews import Feedback, ReviewStore, StoredFinding, StoredReview

BANDS = ((0.0, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01))


@dataclass(frozen=True)
class Rated:
    review: StoredReview
    finding: StoredFinding
    feedback: Feedback

    @property
    def label(self) -> str:
        return self.feedback.label

    @property
    def useful(self) -> bool:
        return self.label == "good"


def rated(store: ReviewStore, days: int = 90, now: datetime | None = None) -> list[Rated]:
    """Posted comments of the last `days` days that people rated (good, bad or mixed)."""
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    out: list[Rated] = []
    for review, finding in store.posted_since(since):
        fb = store.get_feedback(finding.id)
        if fb is not None and fb.label in ("good", "bad", "mixed"):
            out.append(Rated(review, finding, fb))
    return out


def _share(rows: Sequence[Rated]) -> dict[str, Any]:
    good = sum(r.useful for r in rows)
    return {
        "rated": len(rows),
        "useful": good,
        "share": round(good / len(rows), 3) if rows else None,
    }


def _band(conf: float | None) -> str:
    c = 0.0 if conf is None else conf
    for lo, hi in BANDS:
        if lo <= c < hi:
            return f"{lo:.1f}-{min(hi, 1.0):.1f}"
    return "unknown"


def suggest_threshold(rows: Sequence[Rated], target: float = 0.8) -> float | None:
    """The lowest threshold (0.6 to 0.9) at which the comments above it would have been useful
    at least `target` of the time. Only posted comments carry feedback, so this can only say
    whether to raise the threshold, never how low it could go."""
    for t in (0.6, 0.7, 0.8, 0.9):
        kept = [r for r in rows if (r.finding.confidence or 0.0) >= t]
        if kept and sum(r.useful for r in kept) / len(kept) >= target:
            return t
    return None


def report(rows: Sequence[Rated], target: float = 0.8) -> dict[str, Any]:
    groups: dict[str, dict[str, list[Rated]]] = {
        "author": defaultdict(list),
        "category": defaultdict(list),
        "confidence": defaultdict(list),
    }
    for r in rows:
        groups["author"][r.finding.author].append(r)
        groups["category"][r.finding.category or "unlabelled"].append(r)
        groups["confidence"][_band(r.finding.confidence)].append(r)
    labels: dict[str, int] = defaultdict(int)
    for r in rows:
        labels[r.label] += 1
    return {
        "overall": _share(rows),
        "labels": dict(labels),
        "replies": sum(r.feedback.replies for r in rows),
        "resolved": sum(r.feedback.resolved for r in rows),
        **{name: {k: _share(v) for k, v in sorted(g.items())} for name, g in groups.items()},
        "suggested_threshold": suggest_threshold(rows, target),
        "target": target,
    }


def render(rep: dict[str, Any], days: int) -> str:
    def table(title: str, key: str) -> list[str]:
        lines = [
            f"## By {title}",
            "",
            f"| {title.capitalize()} | Rated | Useful |",
            "|---|---|---|",
        ]
        for name, s in rep[key].items():
            lines.append(f"| {name} | {s['rated']} | {s['useful']} ({s['share']:.0%}) |")
        return [*lines, ""]

    o = rep["overall"]
    head = [
        "# PRism comment feedback",
        "",
        f"Posted comments rated in the last {days} days: {o['rated']}.",
        "",
    ]
    if not o["rated"]:
        return "\n".join([*head, "No rated comments yet.", ""])
    suggested = rep["suggested_threshold"]
    advice = (
        f"Comments at confidence {suggested} and above were useful at least "
        f"{rep['target']:.0%} of the time."
        if suggested is not None
        else f"No threshold up to 0.9 keeps the useful share at {rep['target']:.0%}."
    )
    body = [
        f"Useful: {o['useful']} ({o['share']:.0%}). Labels: "
        + ", ".join(f"{k} {v}" for k, v in sorted(rep["labels"].items()))
        + f". Replies: {rep['replies']}; threads resolved: {rep['resolved']}.",
        "",
        advice,
        "",
    ]
    return "\n".join(
        [
            *head,
            *body,
            *table("author", "author"),
            *table("category", "category"),
            *table("confidence", "confidence"),
        ]
    )


def rows_for_export(rows: Iterable[Rated]) -> list[dict[str, Any]]:
    return [
        {
            "repo": r.review.repo,
            "pr": r.review.pr_number,
            "path": r.finding.path,
            "line": r.finding.line,
            "hunk": r.finding.hunk,
            "comment": r.finding.comment,
            "author": r.finding.author,
            "category": r.finding.category,
            "severity": r.finding.severity,
            "confidence": r.finding.confidence,
            "label": r.label,
            "thumbs_up": r.feedback.thumbs_up,
            "thumbs_down": r.feedback.thumbs_down,
            "replies": r.feedback.replies,
            "resolved": r.feedback.resolved,
        }
        for r in rows
    ]


def preference_pairs(rows: Iterable[Rated]) -> list[dict[str, str]]:
    """For each hunk with a liked and a disliked comment: prompt (the hunk), chosen, rejected."""
    by_hunk: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"good": [], "bad": []})
    for r in rows:
        if r.label in ("good", "bad"):
            by_hunk[r.finding.hunk][r.label].append(r.finding.comment)
    return [
        {"prompt": hunk, "chosen": good, "rejected": bad}
        for hunk, side in by_hunk.items()
        for good in side["good"]
        for bad in side["bad"]
    ]


def export(rows: Sequence[Rated], out: Path) -> tuple[int, int]:
    """Write `out` (rated comments) and `<out stem>.pairs.jsonl` (preference pairs)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    items = rows_for_export(rows)
    pairs = preference_pairs(rows)
    out.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
    pairs_path = out.with_name(out.stem + ".pairs.jsonl")
    pairs_path.write_text("".join(json.dumps(p) + "\n" for p in pairs), encoding="utf-8")
    return len(items), len(pairs)
