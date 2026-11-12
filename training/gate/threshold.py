"""Pick the decision threshold on validation scores and report metrics at it.

The gate passes a hunk to the reviewer when its score is at or above the threshold. The threshold
is the one that reaches the target recall with the best precision, so few real issues are skipped.
"Share filtered" is the part of the hunks the gate keeps away from the reviewer; it is what the
gate saves, and a small share is a valid result.

Pure numpy, so the logic is tested without torch.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]


@dataclass(frozen=True)
class Report:
    n: int
    positive_share: float
    threshold: float
    precision: float
    recall: float
    f1: float
    pr_auc: float
    filtered_share: float

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _check(labels: Sequence[int] | Ints, scores: Sequence[float] | Floats) -> tuple[Ints, Floats]:
    y = np.asarray(labels, dtype=np.int64)
    s = np.asarray(scores, dtype=np.float64)
    if y.ndim != 1 or y.shape != s.shape:
        raise ValueError("labels and scores must be 1-D and the same length")
    if y.size == 0:
        raise ValueError("no rows")
    if np.isnan(s).any():
        raise ValueError("scores contain NaN")
    if not np.isin(y, (0, 1)).all():
        raise ValueError("labels must be 0 or 1")
    return y, s


def _curve(y: Ints, s: Floats) -> tuple[Floats, Floats, Floats]:
    """Thresholds (descending, distinct score values), precision and recall at each."""
    positives = int(y.sum())
    if positives == 0:
        raise ValueError("no positive rows: recall is undefined")
    order = np.argsort(-s, kind="stable")
    s_sorted, y_sorted = s[order], y[order]
    tp = np.cumsum(y_sorted)
    fp = np.cumsum(1 - y_sorted)
    # Rows with equal scores are all in or all out: keep the last row of each run of ties.
    last = np.append(np.flatnonzero(np.diff(s_sorted)), s_sorted.size - 1)
    tp, fp, thresholds = tp[last], fp[last], s_sorted[last]
    return thresholds, tp / (tp + fp), tp / positives


def pr_auc(labels: Sequence[int] | Ints, scores: Sequence[float] | Floats) -> float:
    """Average precision, the same definition as scikit-learn's `average_precision_score`."""
    y, s = _check(labels, scores)
    _, precision, recall = _curve(y, s)
    steps = np.diff(np.concatenate(([0.0], recall)))
    return float(np.sum(steps * precision))


def pick_threshold(
    labels: Sequence[int] | Ints,
    scores: Sequence[float] | Floats,
    target_recall: float = 0.90,
) -> float:
    """The score cut-off with recall >= target and the best precision.

    Candidates are the distinct score values (predict positive when score >= cut-off), so ties
    are never split. When several candidates have the same precision, the highest cut-off wins,
    which filters the most. Needs at least one positive row.
    """
    if not 0.0 < target_recall <= 1.0:
        raise ValueError("target_recall must be in (0, 1]")
    y, s = _check(labels, scores)
    thresholds, precision, recall = _curve(y, s)
    ok = np.flatnonzero(recall >= target_recall - 1e-12)
    # Recall reaches 1.0 at the lowest score, so `ok` is never empty.
    best = ok[np.argmax(precision[ok])]
    return float(thresholds[best])


def evaluate(
    labels: Sequence[int] | Ints,
    scores: Sequence[float] | Floats,
    threshold: float,
) -> Report:
    """Metrics at a fixed threshold; precision is 0.0 when nothing is predicted positive."""
    y, s = _check(labels, scores)
    keep = s >= threshold
    tp = int((keep & (y == 1)).sum())
    kept = int(keep.sum())
    positives = int(y.sum())
    precision = tp / kept if kept else 0.0
    recall = tp / positives if positives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return Report(
        n=int(y.size),
        positive_share=positives / y.size,
        threshold=float(threshold),
        precision=precision,
        recall=recall,
        f1=f1,
        pr_auc=pr_auc(y, s) if positives else 0.0,
        filtered_share=1.0 - kept / y.size,
    )


def choose_and_report(
    valid_labels: Sequence[int],
    valid_scores: Sequence[float],
    test_labels: Sequence[int],
    test_scores: Sequence[float],
    target_recall: float = 0.90,
) -> dict[str, object]:
    """Threshold from validation only, then the same threshold on validation and on test."""
    threshold = pick_threshold(valid_labels, valid_scores, target_recall)
    return {
        "target_recall": target_recall,
        "threshold": threshold,
        "validation": evaluate(valid_labels, valid_scores, threshold).as_dict(),
        "test": evaluate(test_labels, test_scores, threshold).as_dict(),
    }


def main(argv: list[str] | None = None) -> None:
    """Recompute the threshold from saved scores: a json file with valid/test labels and scores."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("scores_json", type=Path)
    parser.add_argument("--target-recall", type=float, default=0.90)
    args = parser.parse_args(argv)
    data = json.loads(args.scores_json.read_text(encoding="utf-8"))
    result = choose_and_report(
        data["valid_labels"],
        data["valid_scores"],
        data["test_labels"],
        data["test_scores"],
        args.target_recall,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
