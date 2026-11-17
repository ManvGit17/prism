"""Scoring a BugBench run: recall at the defect's line, false positives on clean PRs, and what
each PR got. Injected and real bugs are reported apart (injected bugs are easier)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from prism.review.checks import Decision
from prism.schemas import ReviewComment

LINE_TOLERANCE = 3


@dataclass
class CaseResult:
    case_id: str
    repo: str
    group: str  # injected, real or clean
    kind: str
    path: str
    lines: list[int]
    hit: bool  # always False for a clean case
    found_by: str | None  # model of the first posted comment that hit
    near_but_dropped: bool  # no hit, but a dropped comment sat near the defect
    posted: int
    considered: int
    first_run_s: float | None
    comments: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def near(path: str, lines: Sequence[int], c_path: str, c_line: int) -> bool:
    return c_path == path and any(abs(c_line - ln) <= LINE_TOLERANCE for ln in lines)


def score_case(
    case_id: str,
    kind: str,
    path: str,
    lines: Sequence[int],
    posted: Sequence[ReviewComment],
    decisions: Sequence[Decision],
    first_run_s: float | None,
    *,
    group: str = "injected",
    repo: str = "",
) -> CaseResult:
    """`posted` are the review's comments, `decisions` everything the verifier saw."""
    hits = [c for c in posted if near(path, lines, c.path, c.line)]
    dropped = [d for d in decisions if d.kept is None]
    return CaseResult(
        case_id=case_id,
        repo=repo,
        group=group,
        kind=kind,
        path=path,
        lines=list(lines),
        hit=bool(hits),
        found_by=hits[0].model if hits else None,
        near_but_dropped=not hits
        and any(near(path, lines, d.candidate.path, d.candidate.raw.line) for d in dropped),
        posted=len(posted),
        considered=len(decisions),
        first_run_s=first_run_s,
        comments=[
            {
                "path": c.path,
                "line": c.line,
                "model": c.model,
                "severity": c.severity.value,
                "category": c.category.value,
                "confidence": c.confidence,
                "comment": c.comment,
                "near_defect": near(path, lines, c.path, c.line),
            }
            for c in posted
        ],
    )


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _recall(results: Sequence[CaseResult]) -> dict[str, Any]:
    n = len(results)
    hits = sum(r.hit for r in results)
    by: dict[str, int] = {}
    for r in results:
        if r.found_by:
            by[r.found_by] = by.get(r.found_by, 0) + 1
    return {
        "cases": n,
        "hits": hits,
        "recall": round(hits / n, 3) if n else None,
        "near_but_dropped": sum(r.near_but_dropped for r in results),
        "comments_per_pr": round(sum(r.posted for r in results) / n, 2) if n else None,
        "found_by": by,
    }


def summarize(results: Sequence[CaseResult]) -> dict[str, Any]:
    injected = [r for r in results if r.group == "injected"]
    real = [r for r in results if r.group == "real"]
    clean = [r for r in results if r.group == "clean"]
    times = [r.first_run_s for r in results if r.first_run_s is not None]
    out: dict[str, Any] = {
        # The headline is injected recall; the slice had only injected cases.
        **_recall(injected),
        "p50_first_run_s": percentile(times, 0.5),
        "p95_first_run_s": percentile(times, 0.95),
    }
    if real:
        out["real"] = _recall(real)
    if clean:
        flagged = sum(r.posted > 0 for r in clean)
        out["clean"] = {
            "cases": len(clean),
            "with_comments": flagged,
            "false_positive_rate": round(flagged / len(clean), 3),
            "comments_per_pr": round(sum(r.posted for r in clean) / len(clean), 2),
        }
    repos = sorted({r.repo for r in results if r.repo})
    if len(repos) > 1:
        out["by_repo"] = {name: _recall([r for r in injected if r.repo == name]) for name in repos}
    return out
