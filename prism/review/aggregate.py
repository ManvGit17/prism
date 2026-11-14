"""The aggregator: cap the kept comments, group them by file, write the summary and verdict."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from prism.review.plan import NotReviewed, ReviewPlan
from prism.schemas import Category, FileReview, ReviewComment, Severity, Verdict

_SEVERITY_RANK = {Severity.CRITICAL: 3, Severity.MAJOR: 2, Severity.MINOR: 1, Severity.INFO: 0}


def rank_key(c: ReviewComment) -> tuple[int, int, float]:
    return (-_SEVERITY_RANK[c.severity], -int(c.category is Category.SECURITY), -c.confidence)


def cap(comments: Sequence[ReviewComment], limit: int) -> tuple[list[ReviewComment], int]:
    """The `limit` most important comments, and how many were cut.

    Comments on the same line in the same category are one finding said twice (Claude and
    Bandit both on an SQL injection): only the strongest is kept, and it is not counted as cut.
    """
    best: dict[tuple[str, int, Category], ReviewComment] = {}
    for c in sorted(comments, key=rank_key):
        best.setdefault((c.path, c.line, c.category), c)
    ordered = sorted(best.values(), key=rank_key)
    return ordered[:limit], max(0, len(ordered) - limit)


def verdict(comments: Sequence[ReviewComment], plan: ReviewPlan, unreviewed: int) -> Verdict:
    if any(c.severity is Severity.CRITICAL for c in comments) or any(
        c.severity is Severity.MAJOR and c.category is Category.SECURITY for c in comments
    ):
        return Verdict.HIGH
    security_unreviewed = unreviewed and any(f.security for f in plan.files if f.over_budget)
    if any(c.severity is Severity.MAJOR for c in comments) or security_unreviewed:
        return Verdict.MEDIUM
    return Verdict.LOW


def summary(
    comments: Sequence[ReviewComment],
    plan: ReviewPlan,
    not_reviewed: Sequence[NotReviewed],
    cut: int,
    verdict_: Verdict,
) -> str:
    reviewed = len(plan.tasks) - sum(1 for n in not_reviewed if n.reason != "budget")
    total = sum(f.hunks_total for f in plan.files)
    gated = sum(f.skipped_by_gate for f in plan.files)
    lines = [
        f"Risk: {verdict_.value}. {len(comments)} comment(s) on {reviewed} reviewed hunk(s) "
        f"of {total} in {len(plan.files)} file(s)."
    ]
    if comments:
        by_sev = Counter(c.severity.value for c in comments)
        lines.append(
            "By severity: "
            + ", ".join(f"{by_sev[s.value]} {s.value}" for s in Severity if by_sev[s.value])
            + "."
        )
    if gated:
        lines.append(f"{gated} hunk(s) skipped by the gate.")
    if cut:
        lines.append(f"{cut} lower-ranked comment(s) left out by the comment cap.")
    if not_reviewed:
        lines.append(f"Not reviewed ({len(not_reviewed)} hunk(s)):")
        lines.extend(f"- {n.path} {n.hunk_header} ({n.reason})" for n in not_reviewed)
    if plan.skipped_files:
        lines.append(
            "Files not reviewed: "
            + ", ".join(f"{p} ({why})" for p, why in plan.skipped_files)
            + "."
        )
    risky = [f for f in plan.files if f.reasons]
    if risky:
        lines.append("Risk signals:")
        lines.extend(f"- {f.path}: {', '.join(f.reasons)}" for f in risky)
    return "\n".join(lines)


def file_reviews(
    plan: ReviewPlan,
    comments: Sequence[ReviewComment],
    not_reviewed: Sequence[NotReviewed],
) -> tuple[FileReview, ...]:
    by_path: dict[str, list[ReviewComment]] = {}
    for c in comments:
        by_path.setdefault(c.path, []).append(c)
    missed = Counter(n.path for n in not_reviewed)
    out: list[FileReview] = []
    for f in plan.files:
        failed_or_late = sum(1 for n in not_reviewed if n.path == f.path and n.reason != "budget")
        out.append(
            FileReview(
                path=f.path,
                comments=tuple(sorted(by_path.get(f.path, []), key=lambda c: c.line)),
                hunks_total=f.hunks_total,
                hunks_reviewed=len(f.tasks) - failed_or_late,
                hunks_skipped_by_gate=f.skipped_by_gate,
                hunks_over_budget=missed[f.path],
            )
        )
    return tuple(out)
